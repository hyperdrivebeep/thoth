"""D-ACTION-STEP-ADD-ID: action/step/add requires a usable step_spec.step_id.

User decision 2026-10-15: option A.
Contract-independent invariants come first: an error response never leaves a published plan
revision, replay and a new-key retry give the same error, and a success response names a step
that really is in the new plan. Then the chosen contract: a missing, empty or non-string step_id
is rejected with DOMAIN_REJECTED "step_spec.step_id is required" before anything is read or
written, with or without dependency_refs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from tests.atomicity.harness import assert_phase_delta, failed_command_allowances, snapshot
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_a03_storage_atomicity import setup

BOOKKEEPING = {"operations", "idempotency_keys", "events", "operation_resource_bindings"}
DOMAIN_REJECTED = -32030
MESSAGE = "step_spec.step_id is required"
BAD_STEP_IDS: dict[str, Any] = {"missing": None, "empty": "", "numeric": 5, "null": None}


async def call(runtime: Any, method: str, key: str, payload: dict[str, Any]) -> dict[str, Any]:
    return value(await runtime.bus.dispatch(request(method, key, payload)))


async def prepare(path: Path) -> tuple[Any, dict[str, Any], dict[str, Any], dict[str, Any]]:
    runtime, ctx = await setup(path)
    generated = await call(
        runtime,
        "action/generate",
        "d-step-actions",
        {
            "project_id": ctx["project"],
            "object_id": ctx["object"],
            "decision_need": "Compare bounded alternatives",
            "evidence_scope": ctx["spans"],
        },
    )
    action = generated["actions"][0]
    step = {
        "step_id": "step:first",
        "action_ref": action["action_id"],
        "inputs": ctx["spans"],
        "output_contract": {"type": "measurement"},
        "preconditions": [],
        "stop_conditions": ["result recorded"],
        "effect_vector": {"effect_completeness_confirmed": True, "external_write": False},
        "state": "READY",
    }
    plan = (
        await call(
            runtime,
            "action/plan/compose",
            "d-step-plan",
            {
                "project_id": ctx["project"],
                "object_id": ctx["object"],
                "selected_action_refs": [action["action_id"]],
                "step_candidates": [step],
                "dependency_edges": [],
                "plan_id": "plan:d",
            },
        )
    )["plan"]
    return runtime, ctx, step, plan


def add_payload(
    ctx: dict[str, Any], step: dict[str, Any], plan: dict[str, Any], kind: str | None, deps: bool
) -> dict[str, Any]:
    spec = {k: v for k, v in step.items() if k != "step_id"}
    if kind in {"empty", "numeric", "null"}:
        spec["step_id"] = BAD_STEP_IDS[kind]
    elif kind is None:
        spec["step_id"] = "step:second"
    return {
        "project_id": ctx["project"],
        "plan_id": "plan:d",
        "step_spec": spec,
        "dependency_refs": ["step:first"] if deps else [],
        "expected_plan_revision": plan["revision_digest"],
    }


async def plan_steps(runtime: Any, ctx: dict[str, Any], key: str) -> list[Any]:
    read = await call(
        runtime, "action/plan/read", key, {"project_id": ctx["project"], "plan_id": "plan:d"}
    )
    return [item.get("step_id") for item in read["plan"]["steps"]]


KINDS = ["missing", "empty", "numeric", "null"]
CASES = [(kind, deps) for kind in KINDS for deps in (False, True)]
IDS = [f"{kind}-{'deps' if deps else 'nodeps'}" for kind, deps in CASES]


@pytest.mark.parametrize(("kind", "deps"), CASES, ids=IDS)
async def test_failed_step_add_never_publishes_a_plan_revision(
    tmp_path: Path, kind: str, deps: bool
) -> None:
    runtime, ctx, step, plan = await prepare(tmp_path)
    command = request("action/step/add", "d-step-bad", add_payload(ctx, step, plan, kind, deps))
    heads_before = dict(runtime.ledger.read_heads(ctx["project"]))
    before = snapshot(runtime.ledger.engine)
    try:
        response = await runtime.bus.dispatch(command)
        after = snapshot(runtime.ledger.engine)
        heads_after = dict(runtime.ledger.read_heads(ctx["project"]))
        if response.error is None:
            # A success must name a step that is in the new plan.
            assert response.result is not None
            named = response.result["value"]["step"]["step_id"]
            assert named in await plan_steps(runtime, ctx, "d-step-read-success")
            return
        domain_changed = sorted(
            name for name in before if name not in BOOKKEEPING and before[name] != after[name]
        )
        assert domain_changed == [], ("error response left published rows", domain_changed)
        assert heads_after == heads_before
        replay = await runtime.bus.dispatch(command)
        assert replay.error == response.error
        assert snapshot(runtime.ledger.engine) == after
        assert await plan_steps(runtime, ctx, "d-step-read-after") == ["step:first"]
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path)
    try:
        retry = await reopened.bus.dispatch(
            request(
                "action/step/add",
                "d-step-bad-retry",
                add_payload(ctx, step, plan, kind, deps),
            )
        )
        assert retry.error is not None and retry.error.code == response.error.code
        assert await plan_steps(reopened, ctx, "d-step-read-reopen") == ["step:first"]
    finally:
        reopened.close()


@pytest.mark.parametrize(("kind", "deps"), CASES, ids=IDS)
async def test_missing_empty_or_non_string_step_id_is_rejected_before_any_read_or_write(
    tmp_path: Path, kind: str, deps: bool
) -> None:
    runtime, ctx, step, plan = await prepare(tmp_path)
    command = request("action/step/add", "d-step-bad", add_payload(ctx, step, plan, kind, deps))
    before = snapshot(runtime.ledger.engine)
    try:
        response = await runtime.bus.dispatch(command)
        after = snapshot(runtime.ledger.engine)
        assert response.error is not None
        assert response.error.code == DOMAIN_REJECTED
        assert response.error.message == MESSAGE
        # Zero reads and zero writes: only the failed operation's bookkeeping differs.
        assert_phase_delta(
            before,
            after,
            failed_command_allowances(runtime.ledger.engine, command, expected_reads=()),
        )
    finally:
        runtime.close()


@pytest.mark.parametrize("deps", [False, True], ids=["nodeps", "deps"])
async def test_step_add_with_a_step_id_still_publishes(tmp_path: Path, deps: bool) -> None:
    runtime, ctx, step, plan = await prepare(tmp_path)
    try:
        result = await call(
            runtime,
            "action/step/add",
            "d-step-good",
            add_payload(ctx, step, plan, None, deps),
        )
        assert result["step"]["step_id"] == "step:second"
        assert await plan_steps(runtime, ctx, "d-step-read-good") == ["step:first", "step:second"]
        edges = result["plan"]["dependency_edges"]
        assert (len(edges) == 1) is deps
    finally:
        runtime.close()
