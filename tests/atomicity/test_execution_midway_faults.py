"""Execution pause, resume, cancel and reconcile each commit their whole transition or nothing.

The dispatch-crash test covers a process killed around the external effect. These cases raise an
exception right after the execution record was written inside the handler. The attempt, effect,
reconciliation and audit rows written before the fault must disappear with it, and the same request
must succeed afterwards.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import JsonValue
from tests.atomicity.harness import assert_phase_delta, failed_command_allowances, snapshot
from tests.integration.scoped_runtime import create_runtime
from tests.integration.source_time_fixture import confirm_synthetic_source_time
from tests.integration.storage_coverage_helpers import request, value

from thoth.application.services.execution_service import ExecutionService
from thoth.apps.runtime import AppRuntime
from thoth.domain.canonical import head_set_digest
from thoth.domain.execution_full import PlanExecutionRecord
from thoth.protocol.jsonrpc import JsonRpcRequest

PROJECT = "project:execution-fault"
EFFECT: dict[str, object] = {"effect_completeness_confirmed": True, "external_write": False}


@dataclass(frozen=True)
class Scene:
    runtime: AppRuntime
    execution_id: str
    execution: dict[str, JsonValue]
    evidence_refs: list[str]


async def call(
    runtime: AppRuntime, method: str, key: str, body: dict[str, object]
) -> dict[str, Any]:
    return value(await runtime.bus.dispatch(request(method, key, body)))


async def build(workspace: Path) -> Scene:
    inbox = workspace / "inbox"
    inbox.mkdir(parents=True)
    note = "# Evidence\n\nThe target-state check is authoritative.\n"
    (inbox / "evidence.md").write_text(note, encoding="utf-8")
    runtime = create_runtime(workspace)
    created: dict[str, object] = {
        "project_id": PROJECT,
        "name": "Execution fault",
        "cutoff_at": "2026-08-31T00:00:00Z",
    }
    await call(runtime, "project/create", "project", created)
    role: dict[str, object] = {
        "project_id": PROJECT,
        "expected_revision": 0,
        "actor_id": "human:project-owner",
        "role": "project-owner",
        "scope": "PROJECT",
        "authority_tags": ["R3_ACTION_OWNER"],
    }
    await call(runtime, "project/role/assign", "role", role)
    source: dict[str, object] = {
        "project_id": PROJECT,
        "relative_path": "evidence.md",
        "media_type": "text/markdown",
        "authority": "OFFICIAL",
        "cutoff_state": "ELIGIBLE",
        "security_class": "INTERNAL",
        "expected_project_revision": 1,
    }
    connected = await call(runtime, "project/source/connect", "source", source)
    await confirm_synthetic_source_time(runtime, PROJECT, connected, key="source")
    listing = await call(runtime, "evidence/list", "evidence", {"project_id": PROJECT})
    evidence_refs = [str(item["span_id"]) for item in listing["spans"]]
    thread: dict[str, object] = {
        "project_id": PROJECT,
        "thread_id": "thread:execution-fault",
        "problem": "Execute a bounded check",
        "scope": {"workstream": "integration"},
    }
    started_thread = await call(runtime, "thread/start", "thread", thread)
    object_id = str(started_thread["current_object_ids"][0])
    action_body: dict[str, object] = {
        "project_id": PROJECT,
        "object_id": object_id,
        "primary_purpose": "INFORMATION_ACQUISITION",
        "specification": {
            "description": "Read target state",
            "expected_observation_or_change": {"description": "target state is observed"},
            "effect_completeness_confirmed": True,
            "stop_conditions": ["state read"],
            "observability": "read receipt",
            "effect_vector": EFFECT,
        },
        "evidence_refs": evidence_refs,
    }
    action = (await call(runtime, "action/create", "action", action_body))["action"]
    step: dict[str, object] = {
        "step_id": "step:read",
        "action_ref": action["action_id"],
        "inputs": evidence_refs,
        "target_digests": ["target:read"],
        "output_contract": {"type": "target-state"},
        "preconditions": [],
        "stop_conditions": ["state read"],
        "effect_vector": EFFECT,
        "state": "READY",
    }
    plan_body: dict[str, object] = {
        "project_id": PROJECT,
        "object_id": object_id,
        "plan_id": "plan:execution-fault",
        "selected_action_refs": [action["action_id"]],
        "step_candidates": [step],
        "dependency_edges": [],
    }
    plan = (await call(runtime, "action/plan/compose", "plan", plan_body))["plan"]
    start: dict[str, object] = {
        "project_id": PROJECT,
        "plan_id": plan["plan_id"],
        "plan_revision_digest": plan["revision_digest"],
        "execution_profile_ref": "execution:local-coordinator-v1",
        "expected_working_head_digest": head_set_digest(runtime.ledger.read_heads(PROJECT)),
    }
    started = await call(runtime, "execution/start", "start", start)
    assert len(started["attempts"]) == 1
    execution = cast(dict[str, JsonValue], started["execution"])
    return Scene(runtime, str(execution["plan_execution_id"]), execution, evidence_refs)


def fail_after_execution_write(patch: pytest.MonkeyPatch) -> dict[str, int]:
    """Raise right after the first execution-record write, when other rows were already written."""
    real = vars(ExecutionService)["_persist_execution"]
    calls = {"n": 0}

    def persist_then_fail(
        self: ExecutionService, record: PlanExecutionRecord, event_type: str
    ) -> None:
        real(self, record, event_type)
        calls["n"] += 1
        raise RuntimeError("injected fault after the execution record write")

    patch.setattr(ExecutionService, "_persist_execution", persist_then_fail)
    return calls


async def expect_all_or_nothing(
    scene: Scene,
    monkeypatch: pytest.MonkeyPatch,
    command: JsonRpcRequest,
    retry: Callable[[], Awaitable[None]],
) -> None:
    engine = scene.runtime.ledger.engine
    before = snapshot(engine)
    with monkeypatch.context() as patch:
        calls = fail_after_execution_write(patch)
        result = await scene.runtime.bus.dispatch(command)
    assert result.error is not None, "the injected fault must surface"
    assert calls["n"] == 1, "the fault point was reached"
    plan_read = ("revision:" + str(scene.execution["plan_revision_digest"]),)
    allowed = failed_command_allowances(engine, command, expected_reads=plan_read)
    assert_phase_delta(before, snapshot(engine), allowed)
    await retry()


async def test_resume_fault_after_the_execution_write_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene = await build(tmp_path / "workspace")
    try:
        pause: dict[str, object] = {
            "project_id": PROJECT,
            "plan_execution_id": scene.execution_id,
            "reason": "inspect",
            "expected_execution_revision": scene.execution["revision"],
        }
        paused = (await call(scene.runtime, "execution/pause", "pause", pause))["execution"]
        body: dict[str, object] = {
            "project_id": PROJECT,
            "plan_execution_id": scene.execution_id,
            "checkpoint_digest": paused["checkpoint_digest"],
            "expected_execution_revision": paused["revision"],
        }

        async def retry() -> None:
            resumed = await call(scene.runtime, "execution/resume", "resume-retry", body)
            assert resumed["execution"]["state"] != "PAUSED_FRONTIER"

        command = request("execution/resume", "resume-fault", body)
        await expect_all_or_nothing(scene, monkeypatch, command, retry)
    finally:
        scene.runtime.close()


async def test_cancel_fault_after_the_execution_write_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene = await build(tmp_path / "workspace")
    try:
        body: dict[str, object] = {
            "project_id": PROJECT,
            "plan_execution_id": scene.execution_id,
            "scope": "step:read",
            "reason": "exercise cancellation",
            "expected_execution_revision": scene.execution["revision"],
        }

        async def retry() -> None:
            cancelled = await call(scene.runtime, "execution/cancel", "cancel-retry", body)
            states = [item["state"] for item in cancelled["affected_attempts"]]
            assert states == ["CANCEL_REQUESTED"]

        command = request("execution/cancel", "cancel-fault", body)
        await expect_all_or_nothing(scene, monkeypatch, command, retry)
    finally:
        scene.runtime.close()


async def test_reconcile_fault_after_the_execution_write_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene = await build(tmp_path / "workspace")
    try:
        cancel: dict[str, object] = {
            "project_id": PROJECT,
            "plan_execution_id": scene.execution_id,
            "scope": "step:read",
            "reason": "prepare reconcile",
            "expected_execution_revision": scene.execution["revision"],
        }
        cancelled = await call(scene.runtime, "execution/cancel", "cancel", cancel)
        attempt = cancelled["affected_attempts"][0]
        body: dict[str, object] = {
            "project_id": PROJECT,
            "plan_execution_id": scene.execution_id,
            "attempt_id": attempt["attempt_id"],
            "target_state_evidence_refs": scene.evidence_refs,
            "reconciliation_method": "TARGET_CHECK_CONFIRMED_NOT_APPLIED",
        }

        async def retry() -> None:
            done = await call(scene.runtime, "execution/reconcile", "reconcile-retry", body)
            assert done["attempt"]["state"] == "FAILED"
            assert done["retry_permission"] is True

        command = request("execution/reconcile", "reconcile-fault", body)
        await expect_all_or_nothing(scene, monkeypatch, command, retry)
    finally:
        scene.runtime.close()
