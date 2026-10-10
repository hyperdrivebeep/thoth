"""Pin acquisition ordering, early exits, partial evidence and collaborator failures."""

import asyncio
from typing import cast

import pytest
from pydantic import JsonValue
from tests.unit.services.acquisition_coordinator_support import (
    HASH,
    T0,
    Harness,
    policy,
    route,
    span,
)

from thoth.domain.connectors import ConnectorErrorCode, ConnectorFailure
from thoth.domain.enums import SufficiencyStatus
from thoth.domain.policy import PolicyDenialBasis, PolicyDenialReceipt


async def execute(h: Harness, evidence_text: str | None = None):
    evidence = () if evidence_text is None else (span("span:existing", evidence_text),)
    return await h.coordinator.execute_if_required(
        project=h.project, thread=h.thread, assessment=h.assessment, evidence=evidence
    )


@pytest.mark.parametrize(
    "case", ["not_requested", "missing_policy", "no_routes", "already_covered"]
)
async def test_no_acquisition_means_no_intent_or_connector_io(case: str) -> None:
    h = Harness((span("span:first", "dataset_version"),))
    if case == "not_requested":
        h.assessment = h.assessment.model_copy(
            update={"derived_status": (SufficiencyStatus.ABSTAIN,)}
        )
    elif case == "missing_policy":
        h.stored_policy = None
    elif case == "no_routes":
        h.stored_policy = policy([])
    assert await execute(h, "DATASET_VERSION" if case == "already_covered" else None) is None
    assert h.events == ([] if case == "not_requested" else ["policy.read"])
    assert h.requests == [] and h.committed == []


async def test_first_missing_route_is_selected_in_policy_order() -> None:
    h = Harness((span("span:first", "other_group"),))
    h.stored_policy = policy([route("dataset_version"), route("other_group"), route("last_group")])
    result = await execute(h, "DATASET_VERSION")
    assert result is not None
    assert h.requests[0].selector == {"relative_path": "other_group.md"}
    assert result.projection["remaining_target_gaps"] == ["last_group"]
    assert result.projection["terminal_state"] == "SUFFICIENT"
    assert len(h.requests) == 1


@pytest.mark.parametrize("first_matches", [True, False])
async def test_first_span_limit_all_acquired_evidence_and_await_commit_order(
    first_matches: bool,
) -> None:
    h = Harness(
        (
            span("span:first", "dataset_version" if first_matches else "unrelated"),
            span("span:later", "dataset_version"),
        )
    )
    result = await execute(h)
    assert result is not None
    assert result.acquired_evidence is h.acquired.ingestion.evidence_candidates
    assert result.projection["acquired_evidence_refs"] == ["span:first"]
    assert h.stage_inputs[0]["span_ids"] == ("span:first",)
    assert (
        h.stage_inputs[0]["observed_statement"]
        == h.acquired.ingestion.evidence_candidates[0].exact_text
    )
    assert len(h.committed) == 1 and h.committed[0].lead is not None
    assert h.committed[0].lead.span_id == "span:first"
    # The original gap check uses all acquired spans, including ones outside the link result limit.
    assert result.projection["remaining_target_gaps"] == []
    assert result.projection["reanalysis_performed"] is True
    usage = cast(dict[str, JsonValue], result.projection["route_usage"])
    assert usage["results_used"] == usage["max_results"] == usage["max_waves"] == 1
    investigation = cast(dict[str, JsonValue], result.projection["investigation"])
    assert investigation["budget_usage"] == investigation["current_wave"] == 1
    assert h.events == [
        "policy.read",
        "investigation.start",
        "intent.put",
        "connector.capabilities",
        "connector.await.enter",
        "connector.await.return",
        "evidence.stage",
        "evidence.commit",
        "investigation.wave",
        "investigation.stop:SUFFICIENT",
    ]
    request = h.requests[0]
    assert request.actor_id == "agent:authorized-evidence-acquisition"
    assert request.max_bytes == 64 * 1024 * 1024 and request.policy_digest == HASH
    assert request.policy_id == "policy:p" and request.policy_revision == 1


async def test_empty_results_saturate_without_staging_or_commit() -> None:
    h = Harness(())
    result = await execute(h)
    assert result is not None and result.acquired_evidence == ()
    assert result.projection["terminal_state"] == "SEARCH_SATURATED"
    assert result.projection["reanalysis_performed"] is False
    assert result.projection["minimum_question"] == "Provide dataset_version evidence."
    assert h.stage_inputs == [] and h.committed == []
    assert h.events[-1] == "investigation.stop:SEARCH_SATURATED"


async def test_partial_evidence_is_committed_and_returned_with_remaining_gap() -> None:
    h = Harness((span("span:first", "unrelated finding"),))
    result = await execute(h)
    assert result is not None
    assert result.projection["terminal_state"] == "SEARCH_SATURATED"
    assert result.projection["remaining_target_gaps"] == ["dataset_version"]
    assert result.projection["reanalysis_performed"] is False
    assert result.acquired_evidence == h.acquired.ingestion.evidence_candidates
    assert len(h.committed) == 1 and h.events[-1] == "investigation.stop:SEARCH_SATURATED"


@pytest.mark.parametrize("kind", ["policy", "driver", "cancelled_receipt"])
async def test_typed_connector_failures_preserve_hold_and_failure_projection(kind: str) -> None:
    h = Harness(())
    denial = (
        PolicyDenialReceipt(
            project_id="p",
            action_kind="CONNECTOR",
            denial_basis=PolicyDenialBasis.SCOPE_DENIED,
            denied_at=T0,
            receipt_digest=HASH,
        )
        if kind == "policy"
        else None
    )
    code = (
        ConnectorErrorCode.CANCELLED
        if kind == "cancelled_receipt"
        else ConnectorErrorCode.DRIVER_ERROR
    )
    h.acquire_failure = ConnectorFailure(code, "controlled failure", policy_denial=denial)
    result = await execute(h)
    assert result is not None and result.acquired_evidence == ()
    assert result.projection["terminal_state"] == ("POLICY_BLOCKED" if denial else "FAILED")
    assert result.projection["reanalysis_performed"] is False
    assert result.projection["policy_denial"] == (
        None if denial is None else denial.model_dump(mode="json")
    )
    assert h.events[-1] == "investigation.stop:" + (
        "PERMISSION_BLOCKED" if denial else "CONNECTOR_FAILED"
    )
    assert h.committed == [] and h.stage_inputs == []


async def test_task_cancellation_propagates_without_fake_terminal_or_commit() -> None:
    h = Harness(())
    h.acquire_failure = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await execute(h)
    assert h.events[-1] == "connector.await.raise"
    assert h.committed == [] and h.stage_inputs == []


async def test_staging_failure_propagates_without_commit() -> None:
    h = Harness((span("span:first", "dataset_version"),))
    h.stage_failure = ValueError("staging failed")
    with pytest.raises(ValueError, match="staging failed"):
        await execute(h)
    assert h.events[-1] == "evidence.stage" and h.committed == []


async def test_commit_failure_returns_all_acquired_spans_and_original_target_gaps() -> None:
    h = Harness((span("span:first", "dataset_version"), span("span:later", "extra")))
    h.commit_failure = RuntimeError("commit failed")
    result = await execute(h)
    assert result is not None
    assert result.projection["terminal_state"] == "FAILED"
    assert result.projection["remaining_target_gaps"] == ["dataset_version"]
    assert result.projection["reanalysis_performed"] is False
    assert result.acquired_evidence is h.acquired.ingestion.evidence_candidates
    assert "observation" not in result.projection and "connector_receipt" not in result.projection
    assert h.events[-2:] == ["evidence.commit", "investigation.stop:EVIDENCE_COMMIT_FAILED"]
    assert h.committed == []


async def test_exhausted_wave_error_after_commit_is_not_retried_or_reclassified() -> None:
    h = Harness((span("span:first", "dataset_version"),))
    h.exhausted_wave = True
    with pytest.raises(ValueError, match="investigation budget is exhausted"):
        await execute(h)
    assert h.events[-2:] == ["evidence.commit", "investigation.wave"]
    assert len(h.committed) == len(h.requests) == 1
