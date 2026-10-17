"""Characterize bounded discovery; doubles record calls, existing route tests cover persistence."""

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel, JsonValue
from tests.integration.test_research_focus_basis import research_host
from tests.integration.test_research_memory_basis import Boundary
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.application.services.connector_service import ConnectorAcquisition, ConnectorService
from thoth.application.services.research_analysis import ResearchAnalysis
from thoth.domain.artifact import ArtifactEnvelope
from thoth.domain.connectors import (
    ConnectorAccessRequest,
    ConnectorArtifactRef,
    ConnectorErrorCode,
    ConnectorFailure,
    ConnectorOperation,
    NativeVersion,
    NativeVersionKind,
)
from thoth.domain.enums import CutoffState, ModelRole, SecurityClass
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.evidence_requirements import (
    CoverageAssessment,
    ResearchSourcePlan,
    SourceSelector,
)
from thoth.domain.model import ContextPack
from thoth.domain.policy import PolicyExpectation
from thoth.domain.project import Project
from thoth.domain.public_web_access import PROJECT_PUBLIC_WEB_CONNECTOR_ID
from thoth.domain.research_execution import ResearchWork
from thoth.domain.research_request import RevisionRef
from thoth.ports.model import ModelPort


@dataclass
class DiscoveryCase:
    analysis: ResearchAnalysis
    project: Project
    model: ControlledResearchModel
    context: ContextPack
    work: ResearchWork
    coverage: CoverageAssessment
    acquired: ConnectorAcquisition


@pytest.fixture
async def discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[DiscoveryCase]:
    captured: list[ConnectorAcquisition] = []
    original = ConnectorService.acquire_one

    async def capture(
        service: ConnectorService, command: ConnectorAccessRequest
    ) -> ConnectorAcquisition:
        result = await original(service, command)
        captured.append(result)
        return result

    model = ControlledResearchModel()
    with monkeypatch.context() as patch:
        patch.setattr(ConnectorService, "acquire_one", capture)
        runtime = await setup(tmp_path, model)
    try:
        host = research_host(runtime)
        project = host.projects.read("p")
        assert project is not None and len(captured) == 1
        ref = RevisionRef(
            project_id="p",
            entity_type="THREAD",
            entity_id="fixture",
            revision_id="fixture",
            revision_digest="a" * 64,
        )
        spans = host.analysis.evidence("p")
        work = ResearchWork(ref, "bounded discovery", Boundary(), evidence=spans)
        context = ContextPack(
            case_id="fixture",
            project_id="p",
            object_id="fixture",
            problem="bounded discovery",
            evidence=spans,
            criteria=(),
            sufficiency=None,
            input_head_set_digest="a" * 64,
        )
        coverage = CoverageAssessment(
            request_ref=ref,
            requirement_set_ref=ref,
            assessments=(),
            web_decision="REQUIRED",
            reasons=(),
            gates={},
            allowed_next_steps=("fixture-gap",),
        )
        yield DiscoveryCase(host.analysis, project, model, context, work, coverage, captured[0])
    finally:
        runtime.close()


@pytest.mark.parametrize(
    "case",
    (
        "catalog-denied",
        "policy-denied",
        "empty-catalog",
        "empty-plan",
        "unauthorized",
        "no-results",
        "auth-fail",
        "driver-fail",
        "partial-read-fail",
        "discover-runtime",
        "read-runtime",
        "bounded",
        "web-url",
        "web-query",
        "web-search",
    ),
)
async def test_discovery_preserves_gates_limits_security_and_exception_scope(
    discovery: DiscoveryCase, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    current = discovery
    analysis = current.analysis
    calls: list[str] = []
    commands: list[ConnectorAccessRequest] = []
    expected_policy = analysis.connectors.current_policy_expectation("p")
    original_artifact = analysis.artifacts.read_artifact
    selected_connector = (
        PROJECT_PUBLIC_WEB_CONNECTOR_ID if case.startswith("web-") else "fixture-connector"
    )
    selector: dict[str, JsonValue] = {
        "mode": "SEARCH" if case == "web-search" else "READ",
        "fixture": "selector",
    }
    if case == "web-query":
        selector["query"] = ""
    count = 6 if case == "bounded" else 2 if case == "partial-read-fail" else 1
    plan = ResearchSourcePlan(
        reason="fixture plan",
        selectors=()
        if case == "empty-plan"
        else tuple(
            SourceSelector.model_validate(
                {
                    "connector_id": "forbidden" if case == "unauthorized" else selected_connector,
                    "selector": {**selector, "index": i},
                }
            )
            for i in range(count)
        ),
    )

    def catalog(project: str) -> tuple[dict[str, JsonValue], ...]:
        assert project == "p"
        calls.append("catalog")
        if case == "catalog-denied":
            raise ConnectorFailure(ConnectorErrorCode.AUTH_REQUIRED, "fixture denial")
        return () if case == "empty-catalog" else ({"connector_id": selected_connector},)

    def policy(project: str) -> PolicyExpectation:
        assert project == "p"
        calls.append("policy")
        if case == "policy-denied":
            raise ConnectorFailure(ConnectorErrorCode.AUTH_REQUIRED, "fixture denial")
        return expected_policy

    async def ask[T: BaseModel](
        model: ModelPort, project: Project, context: ContextPack, role: ModelRole, codec: type[T]
    ) -> tuple[T, str]:
        calls.append("ask")
        assert model is current.model and project is current.project
        assert role is ModelRole.SOURCE_PLANNER and codec is ResearchSourcePlan
        assert context.evidence == current.work.evidence
        assert context.research_context["gaps"] == current.coverage.allowed_next_steps
        return codec.model_validate(plan), "fixture-output"

    def artifact(identifier: str) -> ArtifactEnvelope | None:
        calls.append("artifact:" + identifier)
        record = original_artifact(identifier)
        assert record is not None
        return record.model_copy(update={"security_class": SecurityClass.RESTRICTED})

    async def discover(command: ConnectorAccessRequest) -> tuple[ConnectorArtifactRef, ...]:
        calls.append("discover:" + str(command.selector["index"]))
        commands.append(command)
        if case == "discover-runtime":
            raise RuntimeError("unhandled discovery failure")
        if case in {"auth-fail", "driver-fail"}:
            raise ConnectorFailure(
                ConnectorErrorCode.AUTH_REQUIRED
                if case == "auth-fail"
                else ConnectorErrorCode.DRIVER_ERROR,
                "fixture connector failure",
            )
        if case == "no-results":
            return ()
        return tuple(
            ConnectorArtifactRef(
                source_uri=f"fixture://{i}",
                locator={} if i == 1 else {"resolved": str(i)},
                media_type="text/plain",
                native_version=NativeVersion(kind=NativeVersionKind.NONE),
                observed_at=datetime(2026, 9, 1, tzinfo=UTC),
            )
            for i in range(3)
        )

    async def acquire(command: ConnectorAccessRequest) -> ConnectorAcquisition:
        calls.append("read")
        commands.append(command)
        if case == "read-runtime":
            raise RuntimeError("unhandled read failure")
        if case == "partial-read-fail" and calls.count("read") == 2:
            raise ConnectorFailure(ConnectorErrorCode.AUTH_REQUIRED, "second read denied")
        return current.acquired

    def evidence(project: str) -> tuple[EvidenceSpan, ...]:
        assert project == "p"
        calls.append("evidence")
        return current.work.evidence

    monkeypatch.setattr(analysis.connectors, "research_catalog", catalog)
    monkeypatch.setattr(analysis.connectors, "current_policy_expectation", policy)
    monkeypatch.setattr(analysis, "ask", ask)
    monkeypatch.setattr(analysis.artifacts, "read_artifact", artifact)
    monkeypatch.setattr(analysis.connectors, "discover_candidates", discover)
    monkeypatch.setattr(analysis.connectors, "acquire_one", acquire)
    monkeypatch.setattr(analysis, "evidence", evidence)
    if case in {"discover-runtime", "read-runtime"}:
        with pytest.raises(RuntimeError, match="unhandled"):
            await analysis.discover(
                current.model, current.project, current.context, current.work, current.coverage
            )
        expected = [
            "catalog",
            "policy",
            "ask",
            *("artifact:" + span.artifact_id for span in current.work.evidence),
            "discover:0",
        ]
        assert calls == expected + (["read"] if case == "read-runtime" else [])
        return
    result = await analysis.discover(
        current.model, current.project, current.context, current.work, current.coverage
    )
    if case in {"catalog-denied", "policy-denied", "empty-catalog"}:
        assert calls == (["catalog"] if case == "catalog-denied" else ["catalog", "policy"])
        assert result == (
            {
                "state": "BLOCKED_CAPABILITY",
                "reason": "NO_ALLOWED_DISCOVERY_ROUTE",
                "acquired_count": 0,
            }
            if case == "empty-catalog"
            else {"state": "BLOCKED_ACCESS", "reason": "AUTH_REQUIRED", "acquired_count": 0}
        )
        assert commands == []
        return
    expected = [
        "catalog",
        "policy",
        "ask",
        *("artifact:" + span.artifact_id for span in current.work.evidence),
    ]
    for index in range(min(count, 4)):
        if case in {"unauthorized", "empty-plan"}:
            break
        expected.append("discover:" + str(index))
        if case not in {"no-results", "auth-fail", "driver-fail"}:
            expected.extend(["read", "read"])
    assert calls == [*expected, "evidence"]
    expected_state = {
        "empty-plan": "BLOCKED_CAPABILITY",
        "unauthorized": "BLOCKED_ACCESS",
        "no-results": "SEARCHED_NO_RESULTS",
        "auth-fail": "BLOCKED_ACCESS",
        "driver-fail": "ACQUISITION_FAILED",
    }.get(case, "SEARCHED_BOUNDED")
    assert result["state"] == expected_state
    acquired = (
        8
        if case == "bounded"
        else 3
        if case == "partial-read-fail"
        else 2
        if case.startswith("web-")
        else 0
    )
    assert result["acquired_count"] == acquired
    assert result["source_refs"] == [current.acquired.source.source_id] * acquired
    assert result["eligible_evidence_count"] == sum(
        span.cutoff_state is CutoffState.ELIGIBLE for span in current.work.evidence
    )
    assert result["entire_internet_searched"] is False and result["reason"] == "fixture plan"
    assert result["discovery_kind"] == "REGISTERED_SITE_ENUMERATION"
    if case == "partial-read-fail":
        assert result["failures"] == ["AUTH_REQUIRED"]
        assert result["failure_details"] == [
            {
                "stage": "discover",
                "connector_id": selected_connector,
                "error_code": "AUTH_REQUIRED",
                "reason_code": "second read denied",
            }
        ]
    for command in commands:
        assert command.actor_id == "agent:research-discovery" and command.max_bytes == 2_000_000
        assert (
            command.security_class is SecurityClass.PUBLIC
            and command.cutoff_at == current.project.cutoff_at
        )
        assert command.query_security_class is (
            None if case == "web-url" else SecurityClass.RESTRICTED
        )
        assert all(
            command.model_dump()[key] == item for key, item in expected_policy.model_dump().items()
        )
    for index, command in enumerate(commands):
        if command.operation is ConnectorOperation.READ:
            previous = commands[index - 1]
            if previous.operation is ConnectorOperation.DISCOVER:
                assert command.selector == {"resolved": "0"}
            else:
                assert command.selector == commands[index - 2].selector
