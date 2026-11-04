"""Investigation state changes and their audit entries are written as one unit.

A failure after the state row is written must not leave that row changed without its audit entry,
and the same request must succeed afterwards.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from tests.atomicity.fault_helpers import assert_only_failed_command_bookkeeping
from tests.atomicity.harness import snapshot
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import request, value

from thoth.adapters.storage.investigation import SqliteInvestigationStore
from thoth.apps.runtime import AppRuntime

PROJECT = "project:investigation-faults"


async def call(
    runtime: AppRuntime, method: str, key: str, body: dict[str, object]
) -> dict[str, Any]:
    return value(await runtime.bus.dispatch(request(method, key, body)))


async def open_investigation(workspace: Path) -> tuple[AppRuntime, dict[str, Any]]:
    runtime = create_runtime(workspace)
    created: dict[str, object] = {
        "project_id": PROJECT,
        "name": "Investigations",
        "cutoff_at": "2026-09-01T00:00:00Z",
    }
    await call(runtime, "project/create", "project", created)
    thread: dict[str, object] = {
        "project_id": PROJECT,
        "thread_id": "thread:investigation-faults",
        "problem": "Why did detection fall in rain?",
        "scope": {"workstream": "integration"},
    }
    await call(runtime, "thread/start", "thread", thread)
    listing = await call(runtime, "investigation/list", "list", {"project_id": PROJECT})
    return runtime, listing["investigations"][0]


def fail_store(patch: pytest.MonkeyPatch, method: str) -> dict[str, int]:
    """Make the store method raise instead of writing, so everything before it is what is left."""
    calls = {"n": 0}

    def fail(self: SqliteInvestigationStore, *args: object, **kwargs: object) -> None:
        calls["n"] += 1
        raise RuntimeError("injected investigation store fault at " + method)

    patch.setattr(SqliteInvestigationStore, method, fail)
    return calls


async def read_back(runtime: AppRuntime, investigation_id: str) -> dict[str, Any]:
    body: dict[str, object] = {"project_id": PROJECT, "investigation_id": investigation_id}
    return (await call(runtime, "investigation/read", "read-back", body))["investigation"]


def audit_count(runtime: AppRuntime) -> int:
    return len(snapshot(runtime.ledger.engine)["investigation_audit"])


METHODS = ["pause", "stop", "update", "resume"]


async def request_body(
    runtime: AppRuntime, method: str, current: dict[str, Any]
) -> dict[str, object]:
    base: dict[str, object] = {
        "project_id": PROJECT,
        "investigation_id": current["investigation_id"],
    }
    if method == "stop":
        return {**base, "reason": "USER_STOPPED"}
    if method == "update":
        return {
            **base,
            "expected_plan_revision": current["plan_revision"],
            "budget_extension": 5,
            "reason": "more budget",
        }
    if method == "resume":
        paused = (await call(runtime, "investigation/pause", "pause-first", base))["investigation"]
        return {**base, "expected_checkpoint_digest": paused["checkpoint_digest"]}
    return base


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("point", ["append_audit", "update"])
async def test_state_change_fault_leaves_no_half_change_and_retry_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method: str, point: str
) -> None:
    runtime, current = await open_investigation(tmp_path / "workspace")
    try:
        body = await request_body(runtime, method, current)
        rpc = "investigation/" + method
        command = request(rpc, "fault", body)
        stored_before = await read_back(runtime, current["investigation_id"])
        before = snapshot(runtime.ledger.engine)
        with monkeypatch.context() as patch:
            calls = fail_store(patch, point)
            result = await runtime.bus.dispatch(command)
        assert result.error is not None, "the injected fault must surface"
        assert calls["n"] == 1, "the fault point was reached"
        assert_only_failed_command_bookkeeping(runtime.ledger.engine, before, command)
        assert await read_back(runtime, current["investigation_id"]) == stored_before
        audits = audit_count(runtime)
        retried = await call(runtime, rpc, "retry", body)
        assert retried["investigation"]["plan_revision"] == stored_before["plan_revision"] + 1
        assert audit_count(runtime) == audits + 1
    finally:
        runtime.close()


async def test_start_fault_after_the_record_leaves_no_investigation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, current = await open_investigation(tmp_path / "workspace")
    try:
        body: dict[str, object] = {
            "project_id": PROJECT,
            "thread_id": "thread:investigation-faults",
            "trigger": "COUNTER_SEARCH",
            "question": "Find evidence against the explanation",
        }
        command = request("investigation/start", "fault", body)
        before = snapshot(runtime.ledger.engine)
        with monkeypatch.context() as patch:
            calls = fail_store(patch, "append_audit")
            result = await runtime.bus.dispatch(command)
        assert result.error is not None and calls["n"] == 1
        assert_only_failed_command_bookkeeping(runtime.ledger.engine, before, command)
        listing = await call(runtime, "investigation/list", "after", {"project_id": PROJECT})
        assert [item["investigation_id"] for item in listing["investigations"]] == [
            current["investigation_id"]
        ]
        started = await call(runtime, "investigation/start", "retry", body)
        assert started["investigation"]["trigger"] == "COUNTER_SEARCH"
    finally:
        runtime.close()
