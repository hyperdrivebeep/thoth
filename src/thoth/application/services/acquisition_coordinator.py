from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from pydantic import JsonValue

from thoth.application.services.connector_service import ConnectorService
from thoth.application.services.evidence_graph_service import EvidenceGraphService
from thoth.application.services.investigation_service import InvestigationService
from thoth.domain.acquisition import AcquisitionLead, SearchIntent
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.connectors import ConnectorAccessRequest, ConnectorFailure
from thoth.domain.enums import (
    AuthorityState,
    CutoffState,
    SecurityClass,
    SufficiencyStatus,
)
from thoth.domain.evidence import EvidenceSpan, InformationSufficiencyAssessment
from thoth.domain.policy import AcquisitionRoute, AuthoritativeExecutionPolicy
from thoth.domain.project import Project, WorkThread
from thoth.ports.acquisition import AcquisitionTraceStorePort, EvidenceUnitOfWorkPort
from thoth.ports.governance import ProjectPolicyReaderPort
from thoth.ports.runtime import ClockPort, IdGeneratorPort


@dataclass(frozen=True)
class AutonomousAcquisitionExecution:
    projection: dict[str, JsonValue]
    acquired_evidence: tuple[EvidenceSpan, ...] = ()


class AcquisitionCoordinator:
    def __init__(
        self,
        *,
        connectors: ConnectorService,
        evidence_graph: EvidenceGraphService,
        evidence_unit_of_work: EvidenceUnitOfWorkPort,
        investigations: InvestigationService,
        traces: AcquisitionTraceStorePort,
        policies: ProjectPolicyReaderPort,
        clock: ClockPort,
        ids: IdGeneratorPort,
    ) -> None:
        self._connectors = connectors
        self._evidence_graph = evidence_graph
        self._evidence_unit_of_work = evidence_unit_of_work
        self._investigations = investigations
        self._traces = traces
        self._policies = policies
        self._clock = clock
        self._ids = ids

    async def execute_if_required(
        self,
        *,
        project: Project,
        thread: WorkThread,
        assessment: InformationSufficiencyAssessment,
        evidence: tuple[EvidenceSpan, ...],
    ) -> AutonomousAcquisitionExecution | None:
        if SufficiencyStatus.EVIDENCE_ACQUISITION_REQUIRED not in assessment.derived_status:
            return None
        stored_policy = self._policies.read_policy(project.project_id)
        if stored_policy is None:
            return None
        policy = AuthoritativeExecutionPolicy.from_project_policy(stored_policy)
        target_gaps = self._target_gaps(policy.acquisition_routes, evidence)
        if not target_gaps:
            return None
        route = next(
            item for item in policy.acquisition_routes if item.evidence_group == target_gaps[0]
        )
        investigation = self._investigations.start(
            thread=thread,
            trigger="SUFFICIENCY_GAP",
            question=f"Acquire missing project evidence: {route.evidence_group}",
            target_object_id=assessment.target_object_id,
            mode="BOUNDED",
            scope={
                **thread.scope,
                "cutoff": project.cutoff_at.isoformat(),
                "access": "PROJECT_POLICY_BOUND",
                "egress": "PROJECT_POLICY_BOUND",
                "security": policy.max_source_security_class.value,
            },
            required_evidence_groups=(route.evidence_group,),
            query_families=route.query_families,
            budget=route.max_waves,
            stop_conditions=(
                "TARGET_GAP_CLOSED",
                "SEARCH_SATURATED",
                "POLICY_BLOCKED",
                "BUDGET_EXHAUSTED",
            ),
        )
        intent = self._search_intent(project, thread, investigation.investigation_id, route, policy)
        self._traces.put_search_intent(intent)
        route_capability = next(
            (
                item
                for item in self._connectors.capabilities()
                if item.get("connector_id") == route.connector_id
            ),
            None,
        )
        try:
            acquisition = await self._connectors.acquire_one(
                ConnectorAccessRequest(
                    actor_id="agent:authorized-evidence-acquisition",
                    project_id=project.project_id,
                    connector_id=route.connector_id,
                    selector=route.selector,
                    authority=AuthorityState.UNCLASSIFIED,
                    cutoff_state=CutoffState.ELIGIBLE,
                    security_class=SecurityClass.INTERNAL,
                    cutoff_at=project.cutoff_at,
                    max_bytes=64 * 1024 * 1024,
                    policy_id=policy.policy_id,
                    policy_revision=policy.policy_revision,
                    policy_digest=policy.policy_digest,
                )
            )
        except ConnectorFailure as exc:
            stopped = self._investigations.stop(
                investigation,
                reason=(
                    "PERMISSION_BLOCKED"
                    if exc.policy_denial is not None
                    else "CONNECTOR_FAILED"
                ),
            )
            projection: dict[str, JsonValue] = {
                "terminal_state": (
                    "POLICY_BLOCKED" if exc.policy_denial is not None else "FAILED"
                ),
                "reanalysis_performed": False,
                "minimum_question": (
                    f"Project policy must authorize evidence group {route.evidence_group}."
                ),
                "search_intent": cast(JsonValue, intent.model_dump(mode="json")),
                "investigation": cast(JsonValue, stopped.model_dump(mode="json")),
                "policy_denial": (
                    None
                    if exc.policy_denial is None
                    else cast(JsonValue, exc.policy_denial.model_dump(mode="json"))
                ),
                "route_capability_snapshot": route_capability,
            }
            return AutonomousAcquisitionExecution(projection=projection)

        all_acquired_spans = acquisition.ingestion.evidence_candidates
        spans = all_acquired_spans[: route.max_results]
        if not spans:
            stopped = self._investigations.stop(investigation, reason="SEARCH_SATURATED")
            return AutonomousAcquisitionExecution(
                projection={
                    "terminal_state": "SEARCH_SATURATED",
                    "reanalysis_performed": False,
                    "minimum_question": f"Provide {route.evidence_group} evidence.",
                    "search_intent": cast(JsonValue, intent.model_dump(mode="json")),
                    "investigation": cast(JsonValue, stopped.model_dump(mode="json")),
                }
            )
        span = spans[0]
        lead = self._lead(
            project_id=project.project_id,
            investigation_id=investigation.investigation_id,
            search_intent=intent,
            source_id=acquisition.source.source_id,
            span=span,
        )
        staged = self._evidence_graph.stage_link(
            project_id=project.project_id,
            target_type="DECISION_OBJECT",
            target_id=assessment.target_object_id,
            relation="SUPPORTS",
            span_ids=(span.span_id,),
            observed_statement=span.exact_text,
            thread_id=thread.thread_id,
            conditions={"evidence_group": route.evidence_group},
            applicability="PROJECT_SCOPED_CANDIDATE",
            independence_group=f"connector:{route.connector_id}",
            lead=lead,
        )
        try:
            self._evidence_unit_of_work.commit(staged)
        except Exception:
            stopped = self._investigations.stop(
                investigation,
                reason="EVIDENCE_COMMIT_FAILED",
            )
            return AutonomousAcquisitionExecution(
                projection={
                    "terminal_state": "FAILED",
                    "reanalysis_performed": False,
                    "minimum_question": (
                        f"Retry acquisition for {route.evidence_group} after storage recovery."
                    ),
                    "remaining_target_gaps": list(target_gaps),
                    "search_intent": cast(JsonValue, intent.model_dump(mode="json")),
                    "investigation": cast(JsonValue, stopped.model_dump(mode="json")),
                    "connector_run": cast(
                        JsonValue, acquisition.run.model_dump(mode="json")
                    ),
                },
                acquired_evidence=all_acquired_spans,
            )
        remaining = self._target_gaps(
            policy.acquisition_routes,
            (*evidence, *all_acquired_spans),
        )
        waved = self._investigations.record_wave(
            investigation,
            observation_count=1,
            lead_count=1,
            claim_candidate_count=1,
            remaining_target_gaps=remaining,
        )
        stopped = self._investigations.stop(
            waved,
            reason="SUFFICIENT" if route.evidence_group not in remaining else "SEARCH_SATURATED",
        )
        return AutonomousAcquisitionExecution(
            projection={
                "terminal_state": (
                    "SUFFICIENT"
                    if route.evidence_group not in remaining
                    else "SEARCH_SATURATED"
                ),
                "reanalysis_performed": route.evidence_group not in remaining,
                "minimum_question": (
                    None
                    if route.evidence_group not in remaining
                    else f"Provide {route.evidence_group} evidence."
                ),
                "remaining_target_gaps": list(remaining),
                "search_intent": cast(JsonValue, intent.model_dump(mode="json")),
                "investigation": cast(JsonValue, stopped.model_dump(mode="json")),
                "connector_run": cast(JsonValue, acquisition.run.model_dump(mode="json")),
                "connector_receipt": cast(
                    JsonValue, acquisition.receipt.model_dump(mode="json")
                ),
                "observation": cast(JsonValue, staged.observation.model_dump(mode="json")),
                "lead": cast(JsonValue, lead.model_dump(mode="json")),
                "claim_candidate": cast(
                    JsonValue, staged.claim_candidate.model_dump(mode="json")
                ),
                "acquired_evidence_refs": [item.span_id for item in spans],
                "route_capability_snapshot": route_capability,
                "route_usage": {
                    "connector_id": route.connector_id,
                    "driver_version": acquisition.receipt.driver_version,
                    "native_version_kind": acquisition.receipt.native_version.kind.value,
                    "bytes": acquisition.receipt.byte_size,
                    "results_used": len(spans),
                    "max_results": route.max_results,
                    "max_waves": route.max_waves,
                },
            },
            acquired_evidence=all_acquired_spans,
        )

    def _search_intent(
        self,
        project: Project,
        thread: WorkThread,
        investigation_id: str,
        route: AcquisitionRoute,
        policy: AuthoritativeExecutionPolicy,
    ) -> SearchIntent:
        draft: dict[str, object] = {
            "search_intent_id": self._ids.new("search-intent"),
            "project_id": project.project_id,
            "thread_id": thread.thread_id,
            "investigation_id": investigation_id,
            "evidence_group": route.evidence_group,
            "query_families": route.query_families,
            "connector_id": route.connector_id,
            "selector": route.selector,
            "max_waves": route.max_waves,
            "max_results": route.max_results,
            "stop_conditions": (
                "TARGET_GAP_CLOSED",
                "SEARCH_SATURATED",
                "POLICY_BLOCKED",
                "BUDGET_EXHAUSTED",
            ),
            "policy_id": policy.policy_id,
            "policy_revision": policy.policy_revision,
            "policy_digest": policy.policy_digest,
            "state": "READY",
            "created_at": self._clock.now(),
        }
        return SearchIntent.model_validate(
            {
                **draft,
                "intent_digest": domain_digest(
                    "SEARCH_INTENT", "1.0.0", canonical_payload(draft)
                ),
            }
        )

    def _lead(
        self,
        *,
        project_id: str,
        investigation_id: str,
        search_intent: SearchIntent,
        source_id: str,
        span: EvidenceSpan,
    ) -> AcquisitionLead:
        draft: dict[str, object] = {
            "lead_id": self._ids.new("acquisition-lead"),
            "project_id": project_id,
            "investigation_id": investigation_id,
            "search_intent_id": search_intent.search_intent_id,
            "source_id": source_id,
            "span_id": span.span_id,
            "evidence_group": search_intent.evidence_group,
            "state": "CLAIM_CANDIDATE",
            "information_value": "DECISION_RELEVANT",
            "authority_basis": "project-policy-bound connector source",
            "created_at": self._clock.now(),
        }
        return AcquisitionLead.model_validate(
            {
                **draft,
                "lead_digest": domain_digest(
                    "ACQUISITION_LEAD", "1.0.0", canonical_payload(draft)
                ),
            }
        )

    @staticmethod
    def _target_gaps(
        routes: tuple[AcquisitionRoute, ...],
        evidence: tuple[EvidenceSpan, ...],
    ) -> tuple[str, ...]:
        corpus = "\n".join(span.exact_text.lower() for span in evidence)
        return tuple(
            route.evidence_group
            for route in routes
            if not any(term.lower() in corpus for term in route.match_terms)
        )
