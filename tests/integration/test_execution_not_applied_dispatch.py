"""D-EXECUTION-NOT-APPLIED-REDISPATCH: the system never re-dispatches a FAILED attempt's step.

Contract (user decision 2026-10-14, option A): after reconcile marks an attempt FAILED, the step
waits for an explicit `execution/retry`. Resume, reconcile and sandbox completion of other steps
must not create a new attempt for a FAILED step. INVALIDATED and CANCELLED_CONFIRMED attempts keep
their existing behaviour (still excluded from the "already attempted" set).

Invariants checked on every state: no duplicated (step, attempt_number) or idempotency_key, at most
one live attempt per step, and no live attempt left behind for a completed step.
"""

from __future__ import annotations

import gc
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_a03_storage_atomicity import setup

from thoth.application.services.execution_service import ExecutionService
from thoth.domain.canonical import head_set_digest
from thoth.domain.execution_full import PlanExecutionRecord, StepExecutionAttemptRecord
from thoth.domain.sandbox import (
    SandboxExecutionState,
    SandboxReceipt,
    SandboxResult,
    SandboxRuntimeProfile,
)

LIVE = {"DISPATCHED", "RUNNING", "UNKNOWN_COMPLETION", "CANCEL_REQUESTED"}
NOT_APPLIED = "TARGET_CHECK_CONFIRMED_NOT_APPLIED"
APPLIED = "TARGET_CHECK_CONFIRMED_APPLIED"


async def call(runtime: Any, method: str, key: str, payload: dict[str, Any]) -> dict[str, Any]:
    return value(await runtime.bus.dispatch(request(method, key, payload)))


def r0_step(ctx: dict[str, Any], step_id: str, action: dict[str, Any]) -> dict[str, Any]:
    return {
        "step_id": step_id,
        "action_ref": action["action_id"],
        "inputs": ctx["spans"],
        "output_contract": {"type": "measurement"},
        "preconditions": [],
        "stop_conditions": ["result recorded"],
        "effect_vector": {"effect_completeness_confirmed": True, "external_write": False},
        "state": "READY",
    }


async def begin(path: Path, *, chain: bool) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    """Started R0 execution. chain=True: a -> b; chain=False: independent a and b."""

    runtime, ctx = await setup(path)
    project = ctx["project"]
    generated = await call(
        runtime,
        "action/generate",
        "d-actions",
        {
            "project_id": project,
            "object_id": ctx["object"],
            "decision_need": "Observe a bounded result",
            "evidence_scope": ctx["spans"],
        },
    )
    first, second = generated["actions"][:2]
    plan = (
        await call(
            runtime,
            "action/plan/compose",
            "d-plan",
            {
                "project_id": project,
                "object_id": ctx["object"],
                "plan_id": "plan:d",
                "selected_action_refs": [first["action_id"], second["action_id"]],
                "step_candidates": [r0_step(ctx, "step:a", first), r0_step(ctx, "step:b", second)],
                "dependency_edges": [{"from": "step:a", "to": "step:b"}] if chain else [],
            },
        )
    )["plan"]
    started = await call(
        runtime,
        "execution/start",
        "d-start",
        {
            "project_id": project,
            "plan_id": "plan:d",
            "plan_revision_digest": plan["revision_digest"],
            "execution_profile_ref": "local-metadata-only",
            "expected_working_head_digest": head_set_digest(runtime.ledger.read_heads(project)),
        },
    )
    ctx["plan"] = plan
    return runtime, ctx, started


class Probe:
    """Counts list calls so each read gets a fresh idempotency key."""

    count = 0


async def attempts(runtime: Any, ctx: dict[str, Any], execution_id: str) -> list[dict[str, Any]]:
    Probe.count += 1
    listing = await call(
        runtime,
        "execution/attempt/list",
        f"d-list-{Probe.count}",
        {"project_id": ctx["project"], "plan_execution_id": execution_id},
    )
    return list(listing["attempts"])


def assert_invariants(rows: list[dict[str, Any]], completed: list[str]) -> None:
    numbers = [(row["step_id"], row["attempt_number"]) for row in rows]
    keys = [row["idempotency_key"] for row in rows]
    assert len(numbers) == len(set(numbers)), numbers
    assert len(keys) == len(set(keys)), keys
    for step in {row["step_id"] for row in rows}:
        live = [row for row in rows if row["step_id"] == step and row["state"] in LIVE]
        assert len(live) <= 1, (step, live)
        if step in completed:
            assert live == [], (step, live)


def step_rows(rows: list[dict[str, Any]], step: str) -> list[tuple[int, str]]:
    return sorted((row["attempt_number"], row["state"]) for row in rows if row["step_id"] == step)


async def reconcile(
    runtime: Any, ctx: dict[str, Any], eid: str, attempt_id: str, method: str, key: str
) -> dict[str, Any]:
    return await call(
        runtime,
        "execution/reconcile",
        key,
        {
            "project_id": ctx["project"],
            "plan_execution_id": eid,
            "attempt_id": attempt_id,
            "target_state_evidence_refs": ctx["spans"],
            "reconciliation_method": method,
        },
    )


async def cancel_and_reconcile_not_applied(
    runtime: Any, ctx: dict[str, Any], execution: dict[str, Any], step: str, tag: str
) -> dict[str, Any]:
    """Cancel all dispatched attempts, then confirm that <step> was not applied."""

    eid = execution["plan_execution_id"]
    cancelled = await call(
        runtime,
        "execution/cancel",
        "d-cancel-" + tag,
        {
            "project_id": ctx["project"],
            "plan_execution_id": eid,
            "scope": step,
            "reason": "observe",
            "expected_execution_revision": execution["revision"],
        },
    )
    target = next(a for a in cancelled["affected_attempts"] if a["step_id"] == step)
    return await reconcile(runtime, ctx, eid, target["attempt_id"], NOT_APPLIED, "d-na-" + tag)


async def retry(
    runtime: Any, ctx: dict[str, Any], execution: dict[str, Any], failed: str, key: str
) -> dict[str, Any]:
    return await call(
        runtime,
        "execution/retry",
        key,
        {
            "project_id": ctx["project"],
            "plan_execution_id": execution["plan_execution_id"],
            "step_id": "step:a",
            "failed_attempt_id": failed,
            "retry_reason": "observe",
            "expected_execution_revision": execution["revision"],
        },
    )


async def test_not_applied_waits_for_explicit_retry(tmp_path: Path) -> None:
    runtime, ctx, started = await begin(tmp_path, chain=True)
    try:
        eid = started["execution"]["plan_execution_id"]
        assert step_rows(await attempts(runtime, ctx, eid), "step:a") == [(1, "DISPATCHED")]
        rec = await cancel_and_reconcile_not_applied(
            runtime, ctx, started["execution"], "step:a", "1"
        )
        failed = rec["attempt"]
        assert failed["state"] == "FAILED"
        assert rec["retry_permission"] is True
        # No new dispatch until the user retries.
        assert rec["new_attempts"] == []
        rows = await attempts(runtime, ctx, eid)
        assert step_rows(rows, "step:a") == [(1, "FAILED")]
        assert not any(row["state"] in LIVE for row in rows)
        assert rec["execution"]["state"] == "RUNNING"
        assert len(rec["execution"]["attempt_refs"]) == 1
        assert_invariants(rows, rec["execution"]["completed_steps"])

        retried = await retry(runtime, ctx, rec["execution"], failed["attempt_id"], "d-retry-1")
        assert retried["attempt"]["attempt_number"] == 2
        assert retried["attempt"]["idempotency_key"].endswith(":step:a:2")
        rows = await attempts(runtime, ctx, eid)
        assert step_rows(rows, "step:a") == [(1, "FAILED"), (2, "DISPATCHED")]
        assert_invariants(rows, retried["execution"]["completed_steps"])

        done = await reconcile(
            runtime, ctx, eid, retried["attempt"]["attempt_id"], APPLIED, "d-applied"
        )
        assert done["execution"]["completed_steps"] == ["step:a"]
        rows = await attempts(runtime, ctx, eid)
        assert step_rows(rows, "step:a") == [(1, "FAILED"), (2, "SUCCEEDED")]
        assert step_rows(rows, "step:b") == [(1, "DISPATCHED")]
        assert_invariants(rows, done["execution"]["completed_steps"])
    finally:
        runtime.close()


async def test_retry_that_fails_again_keeps_running_without_dispatch(tmp_path: Path) -> None:
    runtime, ctx, started = await begin(tmp_path, chain=True)
    try:
        eid = started["execution"]["plan_execution_id"]
        first = await cancel_and_reconcile_not_applied(
            runtime, ctx, started["execution"], "step:a", "1"
        )
        retried = await retry(
            runtime, ctx, first["execution"], first["attempt"]["attempt_id"], "d-retry-1"
        )
        second = await reconcile(
            runtime, ctx, eid, retried["attempt"]["attempt_id"], NOT_APPLIED, "d-na-2"
        )
        assert second["attempt"]["state"] == "FAILED"
        assert second["new_attempts"] == []
        # The step waits for the user; the execution state name is unchanged.
        assert second["execution"]["state"] == "RUNNING"
        rows = await attempts(runtime, ctx, eid)
        assert step_rows(rows, "step:a") == [(1, "FAILED"), (2, "FAILED")]
        assert_invariants(rows, second["execution"]["completed_steps"])
        third = await retry(
            runtime, ctx, second["execution"], second["attempt"]["attempt_id"], "d-retry-2"
        )
        assert third["attempt"]["attempt_number"] == 3
        assert_invariants(await attempts(runtime, ctx, eid), third["execution"]["completed_steps"])
    finally:
        runtime.close()


async def test_resume_does_not_redispatch_failed_step(tmp_path: Path) -> None:
    runtime, ctx, started = await begin(tmp_path, chain=True)
    try:
        eid = started["execution"]["plan_execution_id"]
        rec = await cancel_and_reconcile_not_applied(
            runtime, ctx, started["execution"], "step:a", "1"
        )
        paused = await call(
            runtime,
            "execution/pause",
            "d-pause",
            {
                "project_id": ctx["project"],
                "plan_execution_id": eid,
                "reason": "inspect",
                "expected_execution_revision": rec["execution"]["revision"],
            },
        )
        resumed = await call(
            runtime,
            "execution/resume",
            "d-resume",
            {
                "project_id": ctx["project"],
                "plan_execution_id": eid,
                "checkpoint_digest": paused["execution"]["checkpoint_digest"],
                "expected_execution_revision": paused["execution"]["revision"],
            },
        )
        assert resumed["new_attempts"] == []
        rows = await attempts(runtime, ctx, eid)
        assert step_rows(rows, "step:a") == [(1, "FAILED")]
        assert_invariants(rows, resumed["execution"]["completed_steps"])
    finally:
        runtime.close()


async def test_completing_another_step_does_not_redispatch_failed_step(tmp_path: Path) -> None:
    runtime, ctx, started = await begin(tmp_path, chain=False)
    try:
        eid = started["execution"]["plan_execution_id"]
        rec = await cancel_and_reconcile_not_applied(
            runtime, ctx, started["execution"], "step:a", "1"
        )
        assert rec["new_attempts"] == []
        rows = await attempts(runtime, ctx, eid)
        sibling = next(row for row in rows if row["step_id"] == "step:b")
        done = await reconcile(runtime, ctx, eid, sibling["attempt_id"], APPLIED, "d-applied-b")
        assert done["execution"]["completed_steps"] == ["step:b"]
        assert done["new_attempts"] == []
        rows = await attempts(runtime, ctx, eid)
        assert step_rows(rows, "step:a") == [(1, "FAILED")]
        assert_invariants(rows, done["execution"]["completed_steps"])
    finally:
        runtime.close()


def complete_in_sandbox(
    runtime: Any,
    ctx: dict[str, Any],
    execution: dict[str, Any],
    attempt: dict[str, Any],
    state: SandboxExecutionState,
) -> tuple[StepExecutionAttemptRecord, PlanExecutionRecord, tuple[StepExecutionAttemptRecord, ...]]:
    """Public ExecutionService.complete_sandbox_attempt with a hand-made sandbox result."""

    from thoth.domain.action_full import ActionPlanRecord

    # The composition keeps one ExecutionService per runtime; pick the one bound to this ledger.
    (service,) = [
        item
        for item in gc.get_objects()
        if isinstance(item, ExecutionService) and getattr(item, "_ledger", None) is runtime.ledger
    ]
    moment = datetime(2026, 10, 14, tzinfo=UTC)
    result = SandboxResult(
        project_id=ctx["project"],
        attempt_id=attempt["attempt_id"],
        runtime_profile=SandboxRuntimeProfile.SCRIPTED,
        state=state,
        started_at=moment,
        completed_at=moment,
        cleanup_state=SandboxExecutionState.DESTROYED,
    )
    receipt = SandboxReceipt(
        project_id=ctx["project"],
        attempt_id=attempt["attempt_id"],
        runtime_profile=SandboxRuntimeProfile.SCRIPTED,
        image_digest="scripted",
        policy_digest="a" * 64,
        input_digests=(),
        output_digests=(),
        result_state=state,
        cleanup_state=SandboxExecutionState.DESTROYED,
        recorded_at=moment,
        receipt_digest="b" * 64,
    )
    revised, advanced, new_attempts, _commit = service.complete_sandbox_attempt(
        PlanExecutionRecord.model_validate(execution),
        ActionPlanRecord.model_validate(ctx["plan"]),
        StepExecutionAttemptRecord.model_validate(attempt),
        result=result,
        receipt=receipt,
        observation_refs=(),
    )
    return revised, advanced, new_attempts


async def test_sandbox_completion_does_not_redispatch_failed_step(tmp_path: Path) -> None:
    runtime, ctx, started = await begin(tmp_path, chain=False)
    try:
        eid = started["execution"]["plan_execution_id"]
        rows = await attempts(runtime, ctx, eid)
        first = next(row for row in rows if row["step_id"] == "step:a")
        sibling = next(row for row in rows if row["step_id"] == "step:b")
        failed = await reconcile(runtime, ctx, eid, first["attempt_id"], NOT_APPLIED, "d-na-sbx")
        assert failed["new_attempts"] == []
        revised, advanced, new_attempts = complete_in_sandbox(
            runtime, ctx, failed["execution"], sibling, SandboxExecutionState.SUCCEEDED
        )
        assert revised.state == "SUCCEEDED"
        assert advanced.completed_steps == ("step:b",)
        assert new_attempts == ()
        rows = await attempts(runtime, ctx, eid)
        assert step_rows(rows, "step:a") == [(1, "FAILED")]
        assert_invariants(rows, ["step:b"])
    finally:
        runtime.close()


async def test_cancelled_confirmed_step_is_still_dispatched_again(tmp_path: Path) -> None:
    """Unchanged behaviour: CANCELLED_CONFIRMED stays outside the "already attempted" set."""

    runtime, ctx, started = await begin(tmp_path, chain=False)
    try:
        eid = started["execution"]["plan_execution_id"]
        rows = await attempts(runtime, ctx, eid)
        first = next(row for row in rows if row["step_id"] == "step:a")
        sibling = next(row for row in rows if row["step_id"] == "step:b")
        revised, partial, new_attempts = complete_in_sandbox(
            runtime, ctx, started["execution"], first, SandboxExecutionState.CANCELLED
        )
        assert revised.state == "CANCELLED_CONFIRMED"
        assert new_attempts == ()
        done = await reconcile(runtime, ctx, eid, sibling["attempt_id"], APPLIED, "d-applied-sbx")
        assert [item["step_id"] for item in done["new_attempts"]] == ["step:a"]
        assert partial.state == "PARTIAL"
    finally:
        runtime.close()
