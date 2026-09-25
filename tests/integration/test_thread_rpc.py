from __future__ import annotations

from pathlib import Path

import pytest

from thoth.apps.runtime import create_runtime
from thoth.protocol.jsonrpc import JsonRpcRequest


def _request(
    request_id: str,
    method: str,
    idempotency_key: str,
    value: dict[str, object],
) -> JsonRpcRequest:
    return JsonRpcRequest.model_validate(
        {
            "id": request_id,
            "method": method,
            "params": {
                "_meta": {"idempotencyKey": idempotency_key},
                "input": value,
            },
        }
    )


@pytest.mark.asyncio
async def test_project_then_thread_start_and_read_use_same_rpc_bus(tmp_path: Path) -> None:
    runtime = create_runtime(tmp_path / "workspace")
    try:
        created = await runtime.bus.dispatch(
            _request(
                "req-project",
                "project/create",
                "create-project",
                {
                    "project_id": "project:thread-test",
                    "name": "Thread test",
                    "cutoff_at": "2026-08-30T07:05:00Z",
                },
            )
        )
        assert created.error is None
        listed = await runtime.bus.dispatch(
            _request(
                "req-project-list",
                "project/list",
                "list-projects",
                {"project_id": "system:projects"},
            )
        )
        assert listed.error is None
        assert listed.result is not None
        value = listed.result["value"]
        assert isinstance(value, dict)
        projects = value["projects"]
        assert isinstance(projects, list)
        project = projects[0]
        assert isinstance(project, dict)
        assert project["project_id"] == "project:thread-test"
        started = await runtime.bus.dispatch(
            _request(
                "req-thread",
                "thread/start",
                "start-thread",
                {
                    "project_id": "project:thread-test",
                    "thread_id": "thread:blocked-problem",
                    "cycle_id": "cycle:1",
                    "problem": "야간 센서 정확도가 목표보다 낮다",
                },
            )
        )
        read = await runtime.bus.dispatch(
            _request(
                "req-thread-read",
                "thread/read",
                "read-thread",
                {
                    "project_id": "project:thread-test",
                    "thread_id": "thread:blocked-problem",
                },
            )
        )
        paused = await runtime.bus.dispatch(
            _request(
                "req-thread-pause",
                "thread/pause",
                "pause-thread",
                {
                    "project_id": "project:thread-test",
                    "thread_id": "thread:blocked-problem",
                },
            )
        )
        resumed = await runtime.bus.dispatch(
            _request(
                "req-thread-resume",
                "thread/resume",
                "resume-thread",
                {
                    "project_id": "project:thread-test",
                    "thread_id": "thread:blocked-problem",
                },
            )
        )
        stopped = await runtime.bus.dispatch(
            _request(
                "req-thread-stop",
                "thread/stop",
                "stop-thread",
                {
                    "project_id": "project:thread-test",
                    "thread_id": "thread:blocked-problem",
                },
            )
        )
    finally:
        runtime.close()

    assert started.result is not None
    started_value = started.result["value"]
    assert isinstance(started_value, dict)
    assert started_value["problem"] == "야간 센서 정확도가 목표보다 낮다"
    object_ids = started_value["current_object_ids"]
    assert isinstance(object_ids, list)
    assert len(object_ids) == 1
    assert read.result is not None
    assert read.result["value"] == started_value
    assert paused.result is not None
    paused_value = paused.result["value"]
    assert isinstance(paused_value, dict)
    assert paused_value["execution_state"] == "PAUSED"
    assert resumed.result is not None
    resumed_value = resumed.result["value"]
    assert isinstance(resumed_value, dict)
    assert resumed_value["execution_state"] == "IDLE"
    assert stopped.result is not None
    stopped_value = stopped.result["value"]
    assert isinstance(stopped_value, dict)
    assert stopped_value["lifecycle"] == "STOPPED"
