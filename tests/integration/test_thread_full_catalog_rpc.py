from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

from thoth.apps.runtime import create_runtime
from thoth.protocol.jsonrpc import JsonRpcRequest, JsonRpcResponse


def request(method: str, key: str, value: dict[str, object]) -> JsonRpcRequest:
    return JsonRpcRequest.model_validate(
        {
            "id": key,
            "method": method,
            "params": {"_meta": {"idempotencyKey": key}, "input": value},
        }
    )


def value(response: JsonRpcResponse) -> dict[str, JsonValue]:
    assert response.error is None
    assert response.result is not None
    child = response.result["value"]
    assert isinstance(child, dict)
    return cast(dict[str, JsonValue], child)


@pytest.mark.asyncio
async def test_thread_list_activity_checkpoint_steer_fork_and_stop(tmp_path: Path) -> None:
    runtime = create_runtime(tmp_path / "workspace")
    project_id = "project:thread-full"
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "tf-project",
                    {
                        "project_id": project_id,
                        "name": "Thread full catalog",
                        "cutoff_at": "2026-08-31T00:00:00Z",
                    },
                )
            )
        )
        started = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "tf-start",
                    {
                        "project_id": project_id,
                        "thread_id": "thread:full",
                        "display_name": "Initial investigation",
                        "problem": "Why is the integration result blocked?",
                        "scope": {"workstream": "integration"},
                    },
                )
            )
        )
        assert started["revision"] == 0
        listed = value(
            await runtime.bus.dispatch(
                request(
                    "thread/list",
                    "tf-list",
                    {"project_id": project_id},
                )
            )
        )
        assert len(cast(list[object], listed["threads"])) == 1

        steered = value(
            await runtime.bus.dispatch(
                request(
                    "thread/steer",
                    "tf-steer",
                    {
                        "project_id": project_id,
                        "thread_id": "thread:full",
                        "instruction": "Check the measurement timestamp before configuration.",
                    },
                )
            )
        )
        assert isinstance(steered["queued_input"], dict)

        paused = value(
            await runtime.bus.dispatch(
                request(
                    "thread/pause",
                    "tf-pause",
                    {"project_id": project_id, "thread_id": "thread:full"},
                )
            )
        )
        checkpoint = paused["checkpoint"]
        assert isinstance(checkpoint, dict)
        checkpoint_id = str(checkpoint["checkpoint_id"])
        checkpoint_digest = str(checkpoint["checkpoint_digest"])
        assert paused["execution_state"] == "PAUSED"

        checkpoints = value(
            await runtime.bus.dispatch(
                request(
                    "thread/checkpoint/list",
                    "tf-checkpoints",
                    {"project_id": project_id, "thread_id": "thread:full"},
                )
            )
        )
        assert len(cast(list[object], checkpoints["checkpoints"])) == 1
        checkpoint_read = value(
            await runtime.bus.dispatch(
                request(
                    "thread/checkpoint/read",
                    "tf-checkpoint-read",
                    {
                        "project_id": project_id,
                        "thread_id": "thread:full",
                        "checkpoint_id": checkpoint_id,
                    },
                )
            )
        )
        assert isinstance(checkpoint_read["checkpoint"], dict)

        resumed = value(
            await runtime.bus.dispatch(
                request(
                    "thread/resume",
                    "tf-resume",
                    {
                        "project_id": project_id,
                        "thread_id": "thread:full",
                        "expected_checkpoint_digest": checkpoint_digest,
                    },
                )
            )
        )
        assert resumed["execution_state"] == "IDLE"
        assert resumed["revision"] == 2

        metadata = value(
            await runtime.bus.dispatch(
                request(
                    "thread/metadata/update",
                    "tf-metadata",
                    {
                        "project_id": project_id,
                        "thread_id": "thread:full",
                        "expected_revision": 2,
                        "display_name": "Timestamp-first investigation",
                    },
                )
            )
        )
        assert metadata["revision"] == 3

        forked = value(
            await runtime.bus.dispatch(
                request(
                    "thread/fork",
                    "tf-fork",
                    {
                        "project_id": project_id,
                        "thread_id": "thread:full",
                        "child_thread_id": "thread:child",
                    },
                )
            )
        )
        child = forked["thread"]
        assert isinstance(child, dict)
        assert child["parent_thread_id"] == "thread:full"

        activity = value(
            await runtime.bus.dispatch(
                request(
                    "thread/activity/list",
                    "tf-activity",
                    {"project_id": project_id, "thread_id": "thread:full"},
                )
            )
        )
        assert len(cast(list[object], activity["activities"])) >= 5

        stopped = value(
            await runtime.bus.dispatch(
                request(
                    "thread/stop",
                    "tf-stop",
                    {"project_id": project_id, "thread_id": "thread:full"},
                )
            )
        )
        assert stopped["lifecycle"] == "STOPPED"
    finally:
        runtime.close()
