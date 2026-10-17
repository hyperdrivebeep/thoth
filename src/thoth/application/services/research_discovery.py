"""Bounded discovery through the existing authorized connector service."""

from collections.abc import Awaitable, Callable
from typing import Protocol, cast

from pydantic import BaseModel, JsonValue

from thoth.application.services.connector_service import ConnectorService
from thoth.domain.connectors import ConnectorAccessRequest, ConnectorFailure, ConnectorOperation
from thoth.domain.enums import CutoffState, ModelRole, SecurityClass
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.evidence_requirements import CoverageAssessment, ResearchSourcePlan
from thoth.domain.model import ContextPack
from thoth.domain.project import Project
from thoth.domain.public_web_access import PROJECT_PUBLIC_WEB_CONNECTOR_ID
from thoth.domain.research_execution import ResearchWork
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.model import ModelPort


class AskModel(Protocol):
    def __call__[T: BaseModel](
        self,
        model: ModelPort,
        project: Project,
        context: ContextPack,
        role: ModelRole,
        codec: type[T],
    ) -> Awaitable[tuple[T, str]]: ...


async def discover_sources(
    model: ModelPort,
    project: Project,
    context: ContextPack,
    work: ResearchWork,
    coverage: CoverageAssessment,
    *,
    connectors: ConnectorService,
    artifacts: ArtifactLedgerPort,
    ask: AskModel,
    evidence: Callable[[str], tuple[EvidenceSpan, ...]],
) -> dict[str, object]:
    try:
        catalog = connectors.research_catalog(project.project_id)
        expectation = connectors.current_policy_expectation(project.project_id)
    except ConnectorFailure as exc:
        return {"state": "BLOCKED_ACCESS", "reason": exc.code.value, "acquired_count": 0}
    if not catalog:
        return {
            "state": "BLOCKED_CAPABILITY",
            "reason": "NO_ALLOWED_DISCOVERY_ROUTE",
            "acquired_count": 0,
        }
    plan, _ = await ask(
        model,
        project,
        context.model_copy(
            update={
                "evidence": work.evidence,
                "research_context": {
                    **work.context,
                    "catalog": catalog,
                    "gaps": coverage.allowed_next_steps,
                    "task": "Propose bounded discovery selectors "
                    "using only allowed registered catalog contracts. "
                    "Each selector object contains connector_id and selector. "
                    "Do not invent paths, source contents, credentials, SQL or permissions. "
                    "Empty selectors means no supported route; not sufficient evidence.",
                },
            }
        ),
        ModelRole.SOURCE_PLANNER,
        ResearchSourcePlan,
    )
    allowed = {str(c["connector_id"]) for c in catalog}
    security_order = {value: index for index, value in enumerate(SecurityClass)}
    source_classes = [SecurityClass.INTERNAL]
    for span in work.evidence:
        artifact = artifacts.read_artifact(span.artifact_id)
        if artifact is not None:
            source_classes.append(artifact.security_class)
    query_class = max(source_classes, key=security_order.__getitem__)
    acquired: list[str] = []
    failures: list[str] = []
    failure_details: list[dict[str, object]] = []
    discovered = 0
    for item in plan.selectors[:4]:
        connector_id, selector = item.connector_id, item.values()
        if connector_id not in allowed:
            failures.append("SELECTOR_NOT_AUTHORIZED")
            failure_details.append(
                {
                    "stage": "select",
                    "connector_id": str(connector_id),
                    "error_code": "SELECTOR_NOT_AUTHORIZED",
                    "reason_code": "SELECTOR_NOT_AUTHORIZED",
                }
            )
            continue
        try:
            query_security = (
                None
                if connector_id == PROJECT_PUBLIC_WEB_CONNECTOR_ID
                and selector.get("mode") != "SEARCH"
                and "query" not in selector
                else query_class
            )
            request = ConnectorAccessRequest(
                actor_id="agent:research-discovery",
                project_id=project.project_id,
                connector_id=str(connector_id),
                operation=ConnectorOperation.DISCOVER,
                selector=cast(dict[str, JsonValue], selector),
                security_class=SecurityClass.PUBLIC,
                query_security_class=query_security,
                cutoff_at=project.cutoff_at,
                max_bytes=2_000_000,
                **expectation.model_dump(),
            )
            refs = await connectors.discover_candidates(request)
            discovered += len(refs)
            for source in refs[:2]:
                # A discover snippet is never evidence. Re-enter the authorized reader.
                result = await connectors.acquire_one(
                    request.model_copy(
                        update={
                            "operation": ConnectorOperation.READ,
                            "selector": source.locator or request.selector,
                        }
                    )
                )
                acquired.append(result.source.source_id)
        except ConnectorFailure as exc:
            failures.append(exc.code.value)
            failure_details.append(
                {
                    "stage": "discover",
                    "connector_id": str(connector_id),
                    "error_code": exc.code.value,
                    "reason_code": str(exc),
                }
            )
    return {
        "state": (
            "SEARCHED_BOUNDED"
            if acquired
            else "BLOCKED_ACCESS"
            if failures
            and all(
                code
                in {
                    "SCOPE_DENIED",
                    "EGRESS_DENIED",
                    "AUTH_REQUIRED",
                    "AUTH_EXPIRED",
                    "SELECTOR_NOT_AUTHORIZED",
                }
                for code in failures
            )
            else "ACQUISITION_FAILED"
            if failures
            else "SEARCHED_NO_RESULTS"
            if plan.selectors
            else "BLOCKED_CAPABILITY"
        ),
        "acquired_count": len(acquired),
        "discovered_count": discovered,
        "eligible_evidence_count": sum(
            1 for span in evidence(project.project_id) if span.cutoff_state == CutoffState.ELIGIBLE
        ),
        "discovery_kind": "REGISTERED_SITE_ENUMERATION",
        "source_refs": acquired,
        "failures": failures,
        "failure_details": failure_details,
        "reason": plan.reason,
        "entire_internet_searched": False,
    }
