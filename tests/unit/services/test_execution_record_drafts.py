"""Pure Execution record construction after the 93 move: values, ids/clock order, immutability."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest

from thoth.application.services.execution_attempt_drafts import (
    attempt_record,
    revised_attempt_record,
)
from thoth.application.services.execution_preparation import (
    execution_checkpoint,
    execution_resume_token,
)
from thoth.application.services.execution_record_drafts import (
    RECONCILIATION_ATTEMPT_STATES,
    RECONCILIATION_FINDINGS,
    advance_state,
    compensation_specification,
    effect_record,
    reconciliation_record,
    record_digest,
    revised_execution_record,
    sandbox_failed_updates,
    sandbox_not_admitted_updates,
    sandbox_succeeded_updates,
)
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.execution_full import PlanExecutionRecord, StepExecutionAttemptRecord

BASE = datetime(2026, 10, 15, tzinfo=UTC)
PROJECT = "project:u"
PLAN_DIGEST = "a" * 64
HEAD_DIGEST = "b" * 64


class Ports:
    """One log for ids and clock so their relative order is visible."""

    def __init__(self) -> None:
        self.log: list[str] = []
        self.ticks = 0
        self.counter = 0

    def new(self, prefix: str) -> str:
        self.log.append("ids:" + prefix)
        self.counter += 1
        return f"{prefix}:{self.counter}"

    def now(self) -> datetime:
        self.log.append("clock")
        self.ticks += 1
        return BASE + timedelta(seconds=self.ticks)


def execution(**overrides: Any) -> PlanExecutionRecord:
    values: dict[str, Any] = {
        "execution_revision_id": "execution-revision:0",
        "plan_execution_id": "plan-execution:0",
        "project_id": PROJECT,
        "object_id": "object:u",
        "plan_id": "plan:u",
        "plan_revision_digest": PLAN_DIGEST,
        "expected_working_head_digest": HEAD_DIGEST,
        "execution_profile_ref": "local-metadata-only",
        "state": "RUNNING",
        "executable_steps": ("step:a",),
        "blocked_steps": {"step:b": "waiting"},
        "protected_steps": (),
        "prohibited_steps": (),
        "selected_step_ids": (),
        "completed_steps": (),
        "attempt_refs": (),
        "checkpoint_digest": "c" * 64,
        "resume_token": "d" * 64,
        "revision": 0,
        "created_at": BASE,
        "updated_at": BASE,
        "revision_digest": "0" * 64,
    }
    return PlanExecutionRecord.model_validate({**values, **overrides})


def plan(*steps: str) -> Any:
    return cast(
        Any,
        SimpleNamespace(
            revision_digest=PLAN_DIGEST,
            plan_id="plan:u",
            steps=tuple({"step_id": step} for step in steps),
        ),
    )


def attempt(ports: Ports, *, risk: str = "R0", number: int = 1, authorization: Any = None) -> Any:
    return attempt_record(
        execution(),
        plan("step:a"),
        "step:a",
        step={"inputs": ["span:1", "e" * 64], "target_digests": ("f" * 64,)},
        risk=risk,
        authorization=authorization,
        attempt_number=number,
        ids=cast(Any, ports),
        clock=cast(Any, ports),
    )


def test_reconciliation_tables_keep_original_values() -> None:
    assert RECONCILIATION_FINDINGS == {
        "TARGET_CHECK_CONFIRMED_NOT_APPLIED": (
            "CONFIRMED_NOT_APPLIED",
            "NONE",
            True,
            "RETRY_ALLOWED",
        ),
        "TARGET_CHECK_CONFIRMED_APPLIED": (
            "CONFIRMED_APPLIED",
            "CONFIRMED",
            False,
            "LINK_OBSERVATION",
        ),
        "TARGET_CHECK_PARTIAL": (
            "PARTIAL",
            "PARTIAL",
            False,
            "COMPENSATION_OR_NEW_REVISION",
        ),
        "TARGET_CHECK_STILL_UNKNOWN": (
            "STILL_UNKNOWN",
            "EFFECT_MAY_CONTINUE",
            False,
            "RECONCILE_AGAIN",
        ),
    }
    assert RECONCILIATION_ATTEMPT_STATES == {
        "CONFIRMED_NOT_APPLIED": "FAILED",
        "CONFIRMED_APPLIED": "SUCCEEDED",
        "PARTIAL": "PARTIAL",
        "STILL_UNKNOWN": "UNKNOWN_COMPLETION",
    }
    assert {value[0] for value in RECONCILIATION_FINDINGS.values()} == set(
        RECONCILIATION_ATTEMPT_STATES
    )


def test_record_digest_is_the_domain_digest_of_the_canonical_payload() -> None:
    value: dict[str, object] = {"b": 2, "a": ("x",)}
    assert record_digest("KIND", value) == domain_digest("KIND", "1.0.0", canonical_payload(value))


def test_revised_execution_orders_id_before_clock_and_leaves_inputs_alone() -> None:
    ports = Ports()
    current = execution(revision=3, revision_digest="9" * 64)
    updates: dict[str, object] = {"state": "PAUSED_FRONTIER", "executable_steps": ("step:z",)}
    revised = revised_execution_record(
        current, updates, ids=cast(Any, ports), clock=cast(Any, ports)
    )
    assert ports.log == ["ids:execution-revision", "clock"]
    assert revised.revision == 4 and revised.supersedes_revision_digest == "9" * 64
    assert revised.state == "PAUSED_FRONTIER" and revised.executable_steps == ("step:z",)
    checkpoint = execution_checkpoint(
        "plan-execution:0", PLAN_DIGEST, 4, ("step:z",), {"step:b": "waiting"}, ()
    )
    assert revised.checkpoint_digest == checkpoint
    assert revised.resume_token == execution_resume_token(checkpoint, "plan-execution:0")
    assert current.revision == 3 and current.state == "RUNNING"
    assert updates == {"state": "PAUSED_FRONTIER", "executable_steps": ("step:z",)}


@pytest.mark.parametrize(
    ("method", "duplicate_risk"),
    [
        ("TARGET_CHECK_CONFIRMED_NOT_APPLIED", "NONE_CONFIRMED"),
        ("TARGET_CHECK_CONFIRMED_APPLIED", "POSSIBLE"),
        ("TARGET_CHECK_PARTIAL", "POSSIBLE"),
        ("TARGET_CHECK_STILL_UNKNOWN", "POSSIBLE"),
    ],
)
def test_reconciliation_and_effect_records(method: str, duplicate_risk: str) -> None:
    ports = Ports()
    first = attempt(ports)
    ports.log.clear()
    finding, effect_state, retry, next_action = RECONCILIATION_FINDINGS[method]
    refs = ("span:1", "span:2")
    reconciliation = reconciliation_record(
        execution(),
        first,
        method=method,
        evidence_refs=refs,
        finding=finding,
        retry=retry,
        next_action=next_action,
        ids=cast(Any, ports),
        clock=cast(Any, ports),
    )
    effect = effect_record(
        execution(),
        first,
        effect_state=effect_state,
        evidence_refs=refs,
        finding=finding,
        ids=cast(Any, ports),
        clock=cast(Any, ports),
    )
    assert ports.log == ["ids:reconciliation", "clock", "ids:execution-effect", "clock"]
    assert reconciliation.duplicate_risk == duplicate_risk
    assert reconciliation.retry_permitted is retry and reconciliation.finding == finding
    assert reconciliation.next_permitted_action == next_action
    assert reconciliation.target_state_evidence_refs == refs
    assert reconciliation.attempt_id == first.attempt_id == effect.attempt_id
    assert effect.effect_state == effect_state and effect.effect_refs == refs
    assert effect.target_reconciliation_required is (finding == "STILL_UNKNOWN")


def test_compensation_specification_is_the_fixed_mapping() -> None:
    first = attempt(Ports())
    assert compensation_specification("mitigate", first, ("span:1",)) == {
        "description": "mitigate",
        "caused_by_execution_attempt_ref": first.attempt_id,
        "observed_effect_refs": ("span:1",),
        "expected_observation_or_change": {
            "description": "residual effect is reduced and remains observable"
        },
        "effect_completeness_confirmed": False,
        "effect_vector": {"effect_completeness_confirmed": False},
        "stop_conditions": ("residual effect boundary reached",),
        "observability": "effect and residual evidence required",
    }


@pytest.mark.parametrize(
    ("selected", "completed", "executable", "protected", "expected"),
    [
        ((), ("step:a", "step:b"), (), (), "COMPLETED"),
        (("step:a",), ("step:a",), (), (), "COMPLETED"),
        ((), ("step:a",), ("step:b",), ("step:c",), "RUNNING"),
        ((), ("step:a",), (), ("step:b",), "PAUSED_PROTECTED_BOUNDARY"),
        ((), ("step:a",), (), (), "PARTIAL"),
    ],
)
def test_advance_state_follows_the_original_order(
    selected: tuple[str, ...],
    completed: tuple[str, ...],
    executable: tuple[str, ...],
    protected: tuple[str, ...],
    expected: str,
) -> None:
    current = execution(selected_step_ids=selected, completed_steps=completed)
    assert advance_state(plan("step:a", "step:b"), current, executable, protected) == expected


def test_sandbox_updates_do_not_mutate_the_execution() -> None:
    current = execution(
        blocked_steps={"step:b": "waiting"},
        observation_refs=("span:1",),
        completed_steps=("step:z",),
        effect_refs=("effect:1",),
    )
    before = current.model_dump()
    not_admitted = sandbox_not_admitted_updates(current, "step:a", ("span:1", "span:2"), "WHY")
    failed = sandbox_failed_updates(current, "step:a", "TIMED_OUT")
    succeeded = sandbox_succeeded_updates(current, "step:z", "r" * 64, ("span:2", "span:1"))
    assert not_admitted == {
        "state": "PARTIAL",
        "blocked_steps": {"step:b": "waiting", "step:a": "WHY"},
        "observation_refs": ("span:1", "span:2"),
    }
    assert failed == {
        "state": "PARTIAL",
        "blocked_steps": {"step:b": "waiting", "step:a": "TIMED_OUT"},
    }
    assert succeeded == {
        "completed_steps": ("step:z",),
        "effect_refs": ("effect:1", "r" * 64),
        "observation_refs": ("span:1", "span:2"),
    }
    assert current.model_dump() == before


@pytest.mark.parametrize(
    ("risk", "delivery", "effect_state"),
    [
        ("R0", "IDEMPOTENT_RETRYABLE", "NONE"),
        ("R1", "IDEMPOTENT_RETRYABLE", "NONE"),
        ("R2", "IDEMPOTENT_RETRYABLE", "NONE"),
        ("R3", "REQUIRES_RECONCILIATION", "POSSIBLE"),
    ],
)
def test_attempt_record_by_risk_and_call_order(risk: str, delivery: str, effect_state: str) -> None:
    ports = Ports()
    first = attempt(ports, risk=risk, number=2)
    assert ports.log == [
        "ids:attempt-revision",
        "ids:execution-attempt",
        "clock",
        "clock",
        "clock",
    ]
    assert first.state == "DISPATCHED" and first.attempt_number == 2
    assert first.idempotency_key == "plan-execution:0:step:a:2"
    assert first.delivery_guarantee == delivery and first.effect_state == effect_state
    assert first.observation_completeness == "MISSING" and first.authorization_digest is None
    # A 64 character value is kept as given, any other value is hashed.
    assert "e" * 64 in first.input_digests and "span:1" not in first.input_digests
    assert first.target_digests == ("f" * 64,)


def test_attempt_record_environment_depends_on_the_sandbox_risk_only() -> None:
    r0, r1, r2 = (attempt(Ports(), risk=risk) for risk in ("R0", "R1", "R2"))
    assert r0.environment_digest != r1.environment_digest != r2.environment_digest
    assert r2.environment_digest != r0.environment_digest


def test_attempt_record_carries_the_authorization_digest() -> None:
    authorization = SimpleNamespace(exact_scope_digest="9" * 64)
    first = attempt(Ports(), risk="R3", authorization=authorization)
    assert first.authorization_digest == "9" * 64
    assert first.exact_invocation_digest != attempt(Ports(), risk="R3").exact_invocation_digest


def test_revised_attempt_record_links_the_previous_revision() -> None:
    ports = Ports()
    first = attempt(ports)
    ports.log.clear()
    updates: dict[str, object] = {"state": "FAILED", "failure_class": "TRANSIENT"}
    revised = revised_attempt_record(first, updates, ids=cast(Any, ports), clock=cast(Any, ports))
    assert ports.log == ["ids:attempt-revision", "clock"]
    assert revised.supersedes_revision_digest == first.revision_digest
    assert revised.attempt_id == first.attempt_id
    assert revised.attempt_revision_id != first.attempt_revision_id
    assert revised.state == "FAILED" and revised.failure_class == "TRANSIENT"
    assert first.state == "DISPATCHED" and updates == {
        "state": "FAILED",
        "failure_class": "TRANSIENT",
    }
    assert isinstance(revised, StepExecutionAttemptRecord)
