"""Boundary tests for Improvement's pure draft and notification projections."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest

from thoth.application.commands import improvement_control_drafts as drafts
from thoth.application.commands import improvement_control_views as views
from thoth.application.commands.improvement_inputs import (
    EvaluationAssessInput,
    EvaluationPlanInput,
    ExposurePrepareInput,
    ReviseInput,
)
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.control_record import ControlRecord
from thoth.domain.evaluation_run import PairedRunRecord
from thoth.protocol.notifications import notifications_for


def record(kind: str = "REVISION", **payload: object) -> ControlRecord:
    return ControlRecord(
        control_revision_id="control:1",
        project_id="project:unit",
        namespace="IMPROVEMENT",
        record_type=kind,
        record_id="record:" + kind,
        version=1,
        state="PROPOSED",
        payload=payload,
        record_digest="a" * 64,
        created_at=datetime(2026, 10, 15, tzinfo=UTC),
    )


@pytest.mark.parametrize("visible,expected", [(True, True), (False, False), (1, False)])
def test_holdout_access_requires_literal_true(visible: Any, expected: bool) -> None:
    improvement = record(baseline_digest="b" * 64, target_component="PROMPT")
    request = EvaluationPlanInput(
        project_id="project:unit",
        improvement_revision_id=improvement.record_id,
        baseline_digest="b" * 64,
        datasets=({"contains_expected_values": visible},),
        evaluator_refs=(),
        guardrails=(),
        exposure_policy_ref="policy",
    )
    hidden, self_eval, state = drafts.evaluation_plan_state(request, improvement)
    assert hidden is expected and self_eval is False
    assert state == ("HELD" if expected else "PLANNED")


def test_self_evaluator_and_baseline_mismatch_each_hold_plan() -> None:
    improvement = record(
        baseline_digest="b" * 64, target_component="EVALUATOR_CODE", candidate_digest="c" * 64
    )
    request = EvaluationPlanInput(
        project_id="project:unit",
        improvement_revision_id=improvement.record_id,
        baseline_digest="b" * 64,
        datasets=(),
        evaluator_refs=("c" * 64,),
        guardrails=(),
        exposure_policy_ref="policy",
    )
    assert drafts.evaluation_plan_state(request, improvement) == (False, True, "HELD")
    request = request.model_copy(update={"baseline_digest": "d" * 64, "evaluator_refs": ()})
    assert drafts.evaluation_plan_state(request, improvement) == (False, False, "HELD")


@pytest.mark.parametrize("change_content", [False, True])
def test_revision_invalidates_assessment_without_mutating_previous_record(
    change_content: bool,
) -> None:
    current = record(
        candidate_content={"value": 1},
        candidate_digest="c" * 64,
        evaluation_validity="VALID",
        performance_verdict="BETTER",
        promotion_state="PENDING",
    )
    patch: dict[str, Any] = (
        {"candidate_content": {"value": 2}}
        if change_content
        else {"improvement_hypothesis": "updated"}
    )
    request = ReviseInput(
        project_id="project:unit",
        improvement_revision_id=current.record_id,
        expected_revision_digest=current.record_digest,
        patch=patch,
        evidence_refs=(),
        reason="revised",
    )
    before = current.model_dump()
    result = drafts.revised_payload(current, request)
    assert current.model_dump() == before
    assert (
        result["evaluation_validity"],
        result["performance_verdict"],
        result["promotion_state"],
    ) == ("NOT_ASSESSED", "INCONCLUSIVE", "NOT_ELIGIBLE")
    expected = (
        domain_digest("IMPROVEMENT_CANDIDATE", "1.0.0", canonical_payload({"value": 2}))
        if change_content
        else "c" * 64
    )
    assert result["candidate_digest"] == expected


@pytest.mark.parametrize(
    "held,bound,valid,critical,expected",
    [
        (False, True, "VALID", False, ("VALID", "BETTER")),
        (False, True, "VALID", True, ("VALID", "WORSE")),
        (False, True, "INCONCLUSIVE", True, ("INCONCLUSIVE", "INCONCLUSIVE")),
        (True, True, "VALID", False, ("INVALID", "INCONCLUSIVE")),
        (False, False, "VALID", False, ("INCONCLUSIVE", "INCONCLUSIVE")),
    ],
)
def test_assessment_precedence_does_not_turn_unbound_or_invalid_evidence_into_success(
    held: bool, bound: bool, valid: str, critical: bool, expected: tuple[str, str]
) -> None:
    improvement = record(baseline_digest="b" * 64, candidate_digest="c" * 64)
    plan = record(
        "EVALUATION_PLAN",
        improvement_revision_id=improvement.record_id,
        improvement_record_digest=improvement.record_digest,
        baseline_digest="b" * 64,
        candidate_digest="c" * 64,
    )
    plan = plan.model_copy(update={"state": "HELD" if held else "PLANNED"})
    # Value-only double: the helper reads these fields and performs no storage/evaluator calls.
    measured = cast(
        PairedRunRecord,
        SimpleNamespace(
            result=SimpleNamespace(validity=valid, performance_verdict="BETTER"),
            spec=SimpleNamespace(
                plan_id=plan.record_id,
                plan_digest=plan.record_digest if bound else "d" * 64,
                baseline_digest="b" * 64,
                candidate_digest="c" * 64,
            ),
        ),
    )
    assert drafts.assessment_state(plan, improvement, measured, critical) == expected
    assert drafts.assessment_state(plan, improvement, None, critical) == (
        "INVALID" if held else "INCONCLUSIVE",
        "INCONCLUSIVE",
    )


def test_critical_guardrail_field_survives_draft_view_and_notification_consumer() -> None:
    plan = record("EVALUATION_PLAN")
    request = EvaluationAssessInput(
        project_id="project:unit",
        evaluation_plan_id=plan.record_id,
        result_refs=(),
        evaluator_result_refs=(),
        exposure_ledger_ref="e",
        expected_evaluation_revision=1,
    )
    guardrails: tuple[dict[str, object], ...] = ({"critical": True, "status": "FAIL"},)
    payload = drafts.assessment_payload(plan, request, "VALID", "WORSE", guardrails, True, False)
    assessed = plan.model_copy(update={"payload": payload})
    response = views.assessment_response(assessed, "VALID", "WORSE", guardrails, False)
    assert "improvement/guardrailFailed" in notifications_for(
        "improvement/evaluation/assess", response
    )
    assert response["promotion_eligibility"] is False


@pytest.mark.parametrize("stage,authority", [("SHADOW", False), ("CANARY", True)])
def test_exposure_prepare_stays_nonexecuting_and_preserves_notification_key(
    stage: str, authority: bool
) -> None:
    improvement, plan = record(), record("EVALUATION_PLAN")
    request = ExposurePrepareInput(
        project_id="project:unit",
        improvement_revision_id=improvement.record_id,
        evaluation_plan_id=plan.record_id,
        requested_exposure_state=stage,
        scope={},
        duration_budget={},
        stop_rollback_contract={},
    )
    payload = drafts.exposure_payload(request, improvement, plan, authority)
    exposure = record("CANARY_RUN" if authority else "SHADOW_RUN").model_copy(
        update={"payload": payload}
    )
    response = views.exposure_response(exposure)
    assert payload["output_authoritative"] is False
    assert payload["authorization_required"] is authority
    assert response["action_plan_candidate"] == {"owner": "ACTION", "automatic_execution": False}
    notices = notifications_for("improvement/exposure/prepare", response)
    assert ("improvement/canaryPrepared" in notices) is authority
