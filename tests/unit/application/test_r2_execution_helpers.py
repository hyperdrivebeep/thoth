"""Direct tests of the pure R2 closed-loop record and projection builders (98, after the move)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

from thoth.application.services.r2_execution_projections import (
    admission_policy_projection,
    completed_projection,
    failure_projection,
    hold_projection,
    hold_reason_code,
    prepare_blocked_projection,
    sandbox_failure_projection,
    stale_head_projection,
    template_required_projection,
)
from thoth.application.services.r2_recovery_records import (
    assessed_outcome,
    classify_failure,
    closed_loop_actor,
    failure_class_of,
    r2_action,
    reconciliation_record,
    recovery_actor,
    recovery_attempt,
    recovery_candidate,
    repair_basis_text,
    stage_revision,
    terminal_state,
)
from thoth.application.services.r2_test_lifecycle import PendingResultHold
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import ActorKind, EntityType, RiskTier
from thoth.domain.recovery import ExecutionFailureClass, RecoveryAttempt
from thoth.domain.sandbox import SandboxErrorCode, SandboxExecutionState, SandboxFailure

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


def ns(**values: Any) -> Any:
    return cast(Any, SimpleNamespace(**values))


def dumping(**values: Any) -> Any:
    return ns(model_dump=lambda mode="python": {"dumped": values, "mode": mode}, **values)


class Ids:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def new(self, prefix: str) -> str:
        self.calls.append(prefix)
        return f"{prefix}:{len(self.calls):04d}"


class Clock:
    def __init__(self) -> None:
        self.calls = 0

    def now(self) -> datetime:
        self.calls += 1
        return datetime(2026, 10, 15, tzinfo=UTC)


def attempt(
    failure_class: ExecutionFailureClass | None, detail: str | None, ordinal: int = 0
) -> RecoveryAttempt:
    return RecoveryAttempt(
        attempt_id=f"attempt-{ordinal}",
        input_context_digest=DIGEST_A,
        result_state="FAILED",
        failure_class=failure_class,
        failure_detail=detail,
        retry_ordinal=ordinal,
    )


def plan() -> Any:
    return ns(
        plan_id="plan:1",
        alternatives=(
            ns(action_id="a-low", risk_tier=RiskTier.R1, specification="low spec"),
            ns(action_id="a-r2", risk_tier=RiskTier.R2, specification="run it"),
            ns(action_id="a-r2-late", risk_tier=RiskTier.R2, specification="later"),
        ),
        frontier=("a-low", "a-missing", "a-r2-late", "a-r2"),
    )


def test_r2_action_is_the_first_frontier_entry_with_r2_risk() -> None:
    assert cast(Any, r2_action(plan())).action_id == "a-r2-late"
    only_low = ns(alternatives=plan().alternatives, frontier=("a-low", "a-missing"))
    assert r2_action(only_low) is None


def test_failure_classification_follows_marker_priority_and_ignores_case() -> None:
    markers: Any = dict(
        transient_markers=("flaky",),
        semantic_markers=("assumption",),
        code_markers=("syntax",),
        ambiguous_markers=("unknown result",),
    )

    def classify(state: str, detail: str | None) -> ExecutionFailureClass:
        return classify_failure(state, detail, **markers)

    assert classify("FAILED", "Unknown Result and flaky and syntax") == (
        ExecutionFailureClass.AMBIGUOUS_EXTERNAL
    )
    assert classify("FAILED", "bad ASSUMPTION and syntax") == ExecutionFailureClass.SEMANTIC
    assert classify("FAILED", "syntax and flaky") == ExecutionFailureClass.CODE
    assert classify("FLAKY", None) == ExecutionFailureClass.TRANSIENT_INFRA
    assert classify("FAILED", "nothing matches") == ExecutionFailureClass.PERMANENT_INFRA


def test_failure_class_of_a_bundle_is_none_for_a_succeeded_run_and_marker_based_otherwise() -> None:
    policy = ns(
        transient_markers=("boot",),
        semantic_markers=(),
        code_markers=(),
        ambiguous_markers=(),
    )
    succeeded = ns(result=ns(state=SandboxExecutionState.SUCCEEDED, failure_detail="boot"))
    assert failure_class_of(succeeded, policy) is None
    failed = ns(result=ns(state=SandboxExecutionState.FAILED, failure_detail="boot slow"))
    assert failure_class_of(failed, policy) == ExecutionFailureClass.TRANSIENT_INFRA


def test_recovery_attempt_copies_the_bundle_state_and_the_retry_ordinal() -> None:
    spec = ns(attempt_id="attempt-9", input_context_digest=DIGEST_B)
    bundle = ns(result=ns(state=SandboxExecutionState.FAILED, failure_detail="boom"))
    built = recovery_attempt(spec, bundle, ExecutionFailureClass.CODE, 2)
    assert built.model_dump() == {
        "attempt_id": "attempt-9",
        "input_context_digest": DIGEST_B,
        "result_state": SandboxExecutionState.FAILED.value,
        "failure_class": ExecutionFailureClass.CODE,
        "failure_detail": "boom",
        "retry_ordinal": 2,
    }


def test_the_two_actors_keep_their_distinct_identity_and_role() -> None:
    loop, recovery = closed_loop_actor(), recovery_actor()
    assert (loop.actor_id, loop.kind, loop.role) == (
        "agent:r2-closed-loop-coordinator",
        ActorKind.AGENT,
        "trusted-r2-closed-loop-coordinator",
    )
    assert (recovery.actor_id, recovery.kind, recovery.role) == (
        "agent:r2-recovery-coordinator",
        ActorKind.AGENT,
        "bounded-r2-recovery-coordinator",
    )


def test_assessed_outcome_links_the_assessments_and_states_what_stays_unassessed() -> None:
    seen: dict[str, Any] = {}

    def model_copy(*, update: dict[str, Any]) -> Any:
        seen.update(update)
        return "copied"

    outcome = ns(model_copy=model_copy)
    assessments = (ns(assessment_id="x-1"), ns(assessment_id="x-2"))
    assert assessed_outcome(outcome, assessments) == "copied"
    assert seen["test_validity_assessment_refs"] == ("x-1", "x-2")
    assert seen["interpretation"] == "process observation links to scoped test assessments"
    assert seen["limitations"] == (
        "official criterion attainment and causal attribution remain NOT_ASSESSED",
    )


def test_repair_basis_names_the_class_and_falls_back_when_detail_is_missing() -> None:
    assert repair_basis_text(ExecutionFailureClass.CODE, (attempt(None, "syntax error"),)) == (
        "create a new candidate after CODE: syntax error"
    )
    assert repair_basis_text(ExecutionFailureClass.SEMANTIC, (attempt(None, None),)) == (
        "create a new candidate after SEMANTIC: unspecified failure"
    )


def test_terminal_state_gives_ambiguity_first_then_budget_then_candidate_then_failed() -> None:
    cases = (
        (ExecutionFailureClass.AMBIGUOUS_EXTERNAL, True, "RECONCILIATION_REQUIRED"),
        (ExecutionFailureClass.TRANSIENT_INFRA, True, "RETRY_BUDGET_EXHAUSTED"),
        (ExecutionFailureClass.SEMANTIC, False, "CANDIDATE_REVISION_CREATED"),
        (ExecutionFailureClass.CODE, False, "CANDIDATE_REVISION_CREATED"),
        (ExecutionFailureClass.PERMANENT_INFRA, False, "FAILED"),
        (ExecutionFailureClass.TRANSIENT_INFRA, False, "FAILED"),
    )
    for failure_class, exhausted, expected in cases:
        assert terminal_state(failure_class, exhausted) == expected


def test_recovery_candidate_draws_one_id_and_seals_the_draft_digest() -> None:
    ids = Ids()
    attempts = (attempt(ExecutionFailureClass.CODE, "syntax", 0),)
    candidate = recovery_candidate(
        cast(Any, ids), plan(), "a-r2", DIGEST_A, ExecutionFailureClass.CODE, "basis", attempts
    )
    assert ids.calls == ["recovery-candidate"]
    draft: dict[str, object] = {
        "candidate_id": "recovery-candidate:0001",
        "action_id": "a-r2",
        "plan_id": "plan:1",
        "baseline_head_digest": DIGEST_A,
        "failure_class": ExecutionFailureClass.CODE,
        "revised_specification": "run it [candidate revision: CODE]",
        "repair_basis": "basis",
        "attempt_ids": ("attempt-0",),
    }
    assert candidate.candidate_digest == domain_digest(
        "R2_RECOVERY_CANDIDATE", "1.0.0", canonical_payload(draft)
    )
    assert candidate.revised_specification == "run it [candidate revision: CODE]"


def test_reconciliation_record_requires_manual_review_and_uses_the_last_attempt() -> None:
    ids = Ids()
    attempts = (
        attempt(None, "first", 0),
        attempt(ExecutionFailureClass.AMBIGUOUS_EXTERNAL, None, 1),
    )
    record = reconciliation_record(cast(Any, ids), "a-r2", attempts)
    assert ids.calls == ["reconciliation"]
    assert (record.attempt_id, record.state, record.automatic_retry_allowed) == (
        "attempt-1",
        "REQUIRED",
        False,
    )
    assert record.reason == "ambiguous external result"
    draft: dict[str, object] = {
        "reconciliation_id": "reconciliation:0001",
        "action_id": "a-r2",
        "attempt_id": "attempt-1",
        "state": "REQUIRED",
        "automatic_retry_allowed": False,
        "reason": "ambiguous external result",
    }
    assert record.reconciliation_digest == domain_digest(
        "R2_RECONCILIATION", "1.0.0", canonical_payload(draft)
    )


def test_stage_revision_draws_snapshot_then_revision_id_then_the_clock_once() -> None:
    ids, clock = Ids(), Clock()
    content: dict[str, object] = {"value": 1}
    staged = stage_revision(
        cast(Any, ids),
        cast(Any, clock),
        "project:1",
        EntityType.OUTCOME,
        "outcome:1",
        content,
        {"OUTCOME:outcome:1": DIGEST_A},
    )
    assert ids.calls == ["snapshot", "revision"]
    assert clock.calls == 1
    assert staged.revision.snapshot_id == staged.snapshot.snapshot_id
    assert staged.revision.parent_revision_digests == (DIGEST_A,)
    assert staged.snapshot.content_digest == domain_digest(
        "SNAPSHOT", "1.0.0", canonical_payload(content)
    )
    first = stage_revision(
        cast(Any, Ids()), cast(Any, Clock()), "project:1", EntityType.OUTCOME, "o", content, {}
    )
    assert first.revision.parent_revision_digests == ()


def test_hold_reason_code_keeps_a_short_upper_snake_reason_and_masks_everything_else() -> None:
    assert hold_reason_code(ValueError("RESEARCH_TEST_1")) == "RESEARCH_TEST_1"
    for reason in ("", "lower case", "HAS SPACE", "A" * 161, "Mixed_Case"):
        assert hold_reason_code(ValueError(reason)) == "RESEARCH_TEST_INPUT_INVALID"
    assert hold_reason_code(ValueError("A" * 160)) == "A" * 160


def test_hold_projection_adds_pending_detail_only_for_a_pending_result_hold() -> None:
    bundle = ns(receipt=dumping(receipt=1))
    plain = hold_projection("CODE_X", ValueError("x"), bundle)
    assert plain["reason_code"] == "CODE_X"
    assert plain["runtime_io_completed"] is True
    assert plain["sandbox_receipt"] == {"dumped": {"receipt": 1}, "mode": "json"}
    assert "pending_result" not in plain
    pending = hold_projection("CODE_X", PendingResultHold({"why": "unsettled"}), None)
    assert pending["pending_result"] == {"why": "unsettled"}
    assert pending["runtime_io_completed"] is False
    assert pending["sandbox_receipt"] is None


def test_sandbox_failure_projections_distinguish_policy_blocked_from_hold() -> None:
    denial = dumping(denial=1)
    blocked = SandboxFailure(SandboxErrorCode.INPUT_INVALID, "m", policy_denial=denial)
    hold = SandboxFailure(SandboxErrorCode.INPUT_INVALID, "m")
    assert sandbox_failure_projection(blocked, "a-1") == {
        "terminal_state": "POLICY_BLOCKED",
        "action_id": "a-1",
        "policy_denial": {"dumped": {"denial": 1}, "mode": "json"},
        "scientific_truth_state": "NOT_CERTIFIED",
    }
    assert sandbox_failure_projection(hold, "a-1")["terminal_state"] == "HOLD"
    assert sandbox_failure_projection(hold, "a-1")["policy_denial"] is None
    pre_io = prepare_blocked_projection(blocked)
    assert (pre_io["terminal_state"], pre_io["stage"], pre_io["runtime_io_completed"]) == (
        "POLICY_BLOCKED",
        "PRE_IO",
        False,
    )
    assert prepare_blocked_projection(hold)["terminal_state"] == "HOLD"
    assert prepare_blocked_projection(hold)["reason_code"] == SandboxErrorCode.INPUT_INVALID.value


def test_template_stale_and_admission_projections_carry_the_action_and_the_sandbox_result() -> None:
    assert template_required_projection("a-1") == {
        "terminal_state": "HOLD",
        "reason_code": "SANDBOX_TEMPLATE_REQUIRED",
        "action_id": "a-1",
        "scientific_truth_state": "NOT_CERTIFIED",
    }
    bundle = ns(result=dumping(result=1), receipt=dumping(receipt=1))
    stale = stale_head_projection("a-1", bundle)
    assert stale["reason_code"] == "SANDBOX_HEAD_STALE_AFTER_IO"
    assert stale["sandbox_result"] == {"dumped": {"result": 1}, "mode": "json"}
    assert stale["sandbox_receipt"] == {"dumped": {"receipt": 1}, "mode": "json"}
    admission = admission_policy_projection("a-1", dumping(denial=1), bundle)
    assert admission["reason_code"] == "SANDBOX_ADMISSION_POLICY_CHANGED"
    assert admission["stage"] == "POST_IO_ADMISSION"
    assert admission["policy_denial"] == {"dumped": {"denial": 1}, "mode": "json"}
    assert "sandbox_receipt" not in admission


def test_failure_projection_reports_attempts_the_receipt_and_the_test_lifecycle_when_present() -> (
    None
):
    attempts = (
        attempt(ExecutionFailureClass.CODE, "x", 0),
        attempt(ExecutionFailureClass.CODE, "y", 1),
    )
    commit = ns(branch_revision_ids=("r-1",), receipt=dumping(receipt=1))
    projection = failure_projection(
        "CANDIDATE_REVISION_CREATED",
        None,
        "a-1",
        attempts,
        DIGEST_A,
        DIGEST_B,
        commit,
        "basis",
        None,
        False,
    )
    recovery = cast(dict[str, Any], projection["recovery"])
    assert projection["terminal_state"] == "CANDIDATE_REVISION_CREATED"
    assert "test_lifecycle" not in projection
    assert recovery["retry_count"] == 1
    assert (recovery["baseline_head_before"], recovery["baseline_head_after"]) == (
        DIGEST_A,
        DIGEST_B,
    )
    assert recovery["candidate_branch_revision_ids"] == ["r-1"]
    assert recovery["reconciliation"] is None
    with_test = failure_projection(
        "FAILED",
        ns(projection={"p": 1}),
        "a-1",
        attempts[:1],
        DIGEST_A,
        DIGEST_B,
        commit,
        "basis",
        dumping(reconciliation=1),
        True,
    )
    assert with_test["test_lifecycle"] == {"p": 1}
    with_recovery = cast(dict[str, Any], with_test["recovery"])
    assert with_recovery["retry_count"] == 0
    assert with_recovery["retry_budget_exhausted"] is True
    assert with_recovery["reconciliation"] == {"dumped": {"reconciliation": 1}, "mode": "json"}


def test_completed_projection_is_not_certified_and_reports_the_given_retry_count() -> None:
    process = ns(
        observation_refs=("s-1",),
        next_order=("a-2",),
        validity=dumping(validity=1),
        appraisal=dumping(appraisal=1),
    )
    spec = dumping(spec=1, current_head_set_digest=DIGEST_A)
    bundle = ns(result=dumping(result=1), receipt=dumping(receipt=1))
    commit = ns(receipt=dumping(receipt=2), after_head_set_digest=DIGEST_B)
    projection = completed_projection(
        None, spec, bundle, process, dumping(outcome=1), commit, (attempt(None, None),), 3
    )
    recovery = cast(dict[str, Any], projection["recovery"])
    assert projection["terminal_state"] == "COMPLETED"
    assert projection["scientific_truth_state"] == "NOT_CERTIFIED"
    assert projection["observation_refs"] == ["s-1"]
    assert projection["next_action_order"] == ["a-2"]
    assert "test_lifecycle" not in projection
    assert recovery["retry_count"] == 3
    assert (recovery["baseline_head_before"], recovery["baseline_head_after"]) == (
        DIGEST_A,
        DIGEST_B,
    )
    assert recovery["retry_budget_exhausted"] is False
    linked = completed_projection(
        ns(projection={"p": 1}), spec, bundle, process, dumping(outcome=1), commit, (), 0
    )
    assert linked["test_lifecycle"] == {"p": 1}
    assert next(iter(linked)) == "test_lifecycle"
