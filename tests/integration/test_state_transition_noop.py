"""A repeated pause, resume or stop returns the state that is already in place (D-S1).

Thread (without a research request) and investigation commands: a repeat under a new key returns
the current state with unchanged=true and an unchanged_reason, adds no revision, activity,
checkpoint or audit row and sends no notification. A guard (the checkpoint digest) is still checked
first, and an unsupported stop reason is still refused. Fake runtime only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import request, value

from thoth.apps.runtime import AppRuntime
from thoth.protocol.notifications import notifications_for

SCOPE: dict[str, object] = {"project_id": "p", "thread_id": "th"}


async def _start(runtime: AppRuntime) -> None:
    value(
        await runtime.bus.dispatch(
            request(
                "project/create",
                "p",
                {"project_id": "p", "name": "X", "cutoff_at": "2026-08-31T00:00:00Z"},
            )
        )
    )
    value(
        await runtime.bus.dispatch(
            request(
                "thread/start",
                "t",
                {**SCOPE, "problem": "Why?", "scope": {"workstream": "x"}},
            )
        )
    )


async def _written(runtime: AppRuntime) -> tuple[int, int]:
    activities = value(await runtime.bus.dispatch(request("thread/activity/list", "a", SCOPE)))
    checkpoints = value(await runtime.bus.dispatch(request("thread/checkpoint/list", "c", SCOPE)))
    return len(activities["activities"]), len(checkpoints["checkpoints"])


def _operation_events(runtime: AppRuntime, operation_id: str) -> list[str]:
    with runtime.ledger.engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT event_type FROM events WHERE operation_id = ?", (operation_id,)
        ).all()
    return [str(row[0]) for row in rows]


async def _send(runtime: AppRuntime, method: str, key: str, **fields: object) -> dict[str, Any]:
    response = await runtime.bus.dispatch(request(method, key, {**SCOPE, **fields}))
    assert response.error is None and response.result is not None, response.error
    answered = response.result["value"]
    assert isinstance(answered, dict)
    return {**answered, "_operation": response.result["operation_id"]}


async def test_a_repeated_thread_pause_resume_and_stop_write_nothing(tmp_path: Path) -> None:
    runtime = create_runtime(tmp_path / "workspace")
    try:
        await _start(runtime)
        paused = await _send(runtime, "thread/pause", "pause-1")
        assert paused["execution_state"] == "PAUSED" and "unchanged" not in paused
        digest = paused["checkpoint"]["checkpoint_digest"]
        before = await _written(runtime)

        again = await _send(runtime, "thread/pause", "pause-2")
        assert again["unchanged"] is True and again["unchanged_reason"] == "THREAD_ALREADY_PAUSED"
        assert again["execution_state"] == "PAUSED" and again["revision"] == paused["revision"]
        assert again["checkpoint"] == paused["checkpoint"]
        assert await _written(runtime) == before
        assert not [e for e in _operation_events(runtime, again["_operation"]) if "/" in e]

        # The guard is checked first: a stale digest is still refused while paused.
        stale = await runtime.bus.dispatch(
            request("thread/resume", "stale", {**SCOPE, "expected_checkpoint_digest": "0" * 64})
        )
        assert stale.error is not None

        resumed = await _send(
            runtime, "thread/resume", "resume-1", expected_checkpoint_digest=digest
        )
        assert resumed["execution_state"] == "IDLE" and "unchanged" not in resumed
        before = await _written(runtime)
        # Resuming again with the same digest (still the latest checkpoint) changes nothing.
        repeated = await _send(
            runtime, "thread/resume", "resume-2", expected_checkpoint_digest=digest
        )
        assert repeated["unchanged"] is True
        assert repeated["unchanged_reason"] == "THREAD_ALREADY_RESUMED"
        assert repeated["revision"] == resumed["revision"]
        assert await _written(runtime) == before
        assert not [e for e in _operation_events(runtime, repeated["_operation"]) if "/" in e]

        stopped = await _send(runtime, "thread/stop", "stop-1")
        assert stopped["lifecycle"] == "STOPPED" and "unchanged" not in stopped
        before = await _written(runtime)
        stopped_again = await _send(runtime, "thread/stop", "stop-2")
        assert stopped_again["unchanged"] is True
        assert stopped_again["unchanged_reason"] == "THREAD_ALREADY_STOPPED"
        assert stopped_again["revision"] == stopped["revision"]
        assert await _written(runtime) == before
        assert not [e for e in _operation_events(runtime, stopped_again["_operation"]) if "/" in e]
    finally:
        runtime.close()


async def test_resuming_a_thread_that_was_never_paused_changes_nothing(tmp_path: Path) -> None:
    runtime = create_runtime(tmp_path / "workspace")
    try:
        await _start(runtime)
        before = await _written(runtime)
        resumed = await _send(runtime, "thread/resume", "resume")
        assert resumed["unchanged"] is True
        assert resumed["unchanged_reason"] == "THREAD_ALREADY_RESUMED"
        assert resumed["revision"] == 0
        assert await _written(runtime) == before
    finally:
        runtime.close()


async def test_a_repeated_investigation_pause_writes_nothing(tmp_path: Path) -> None:
    runtime = create_runtime(tmp_path / "workspace")
    try:
        await _start(runtime)
        listed = value(await runtime.bus.dispatch(request("investigation/list", "l", SCOPE)))
        scope: dict[str, object] = {
            "project_id": "p",
            "investigation_id": listed["investigations"][0]["investigation_id"],
        }

        async def audit_count() -> int:
            read = value(
                await runtime.bus.dispatch(
                    request("investigation/audit/read", "audit-" + str(id(object())), scope)
                )
            )
            return len(read["records"])

        paused = value(await runtime.bus.dispatch(request("investigation/pause", "p1", scope)))
        assert paused["investigation"]["execution_state"] == "PAUSED"
        before = await audit_count()
        again = value(await runtime.bus.dispatch(request("investigation/pause", "p2", scope)))
        assert again["unchanged"] is True
        assert again["unchanged_reason"] == "INVESTIGATION_ALREADY_PAUSED"
        assert again["investigation"] == paused["investigation"]
        assert await audit_count() == before
    finally:
        runtime.close()


def test_an_unchanged_result_announces_nothing_and_a_changed_one_still_does() -> None:
    assert notifications_for("thread/resume", {"unchanged": True}) == ()
    assert notifications_for("thread/stop", {"unchanged": True, "cancelled": False}) == ()
    assert "thread/resumed" in notifications_for("thread/resume", {})
    assert "thread/resumed" in notifications_for("thread/resume", {"unchanged": False})
