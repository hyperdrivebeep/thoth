from __future__ import annotations

from pathlib import Path

import pytest

from thoth.apps.runtime import create_runtime
from thoth.protocol.jsonrpc import JsonRpcRequest, RpcErrorCode


def _rpc(method: str, key: str, value: dict[str, object]) -> JsonRpcRequest:
    return JsonRpcRequest.model_validate(
        {
            "id": key,
            "method": method,
            "params": {"_meta": {"idempotencyKey": key}, "input": value},
        }
    )


def _project_request() -> JsonRpcRequest:
    return _rpc(
        "project/create",
        "response-loss-project",
        {
            "project_id": "project:response-loss",
            "name": "Response loss",
            "cutoff_at": "2026-08-30T12:00:00Z",
        },
    )


@pytest.mark.asyncio
async def test_response_loss_replay_survives_process_restart(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    first_runtime = create_runtime(workspace)
    try:
        first = await first_runtime.bus.dispatch(_project_request())
    finally:
        first_runtime.close()

    second_runtime = create_runtime(workspace)
    try:
        replay = await second_runtime.bus.dispatch(_project_request())
    finally:
        second_runtime.close()

    assert first.model_dump(mode="json") == replay.model_dump(mode="json")


@pytest.mark.asyncio
async def test_stale_checkpoint_resume_is_rejected_and_current_checkpoint_is_fail_closed(
    tmp_path: Path,
) -> None:
    runtime = create_runtime(tmp_path / "workspace")
    try:
        created = await runtime.bus.dispatch(_project_request())
        assert created.result is not None
        operation_id = created.result["operation_id"]
        assert isinstance(operation_id, str)
        stale = await runtime.bus.dispatch(
            _rpc(
                "operation/resume",
                "stale-resume",
                {
                    "project_id": "project:response-loss",
                    "operation_id": operation_id,
                    "expected_checkpoint_digest": "0" * 64,
                },
            )
        )
        read = await runtime.bus.dispatch(
            _rpc(
                "operation/read",
                "read-checkpoint",
                {
                    "project_id": "project:response-loss",
                    "operation_id": operation_id,
                },
            )
        )
        assert read.result is not None
        read_value = read.result["value"]
        assert isinstance(read_value, dict)
        checkpoint = read_value["latest_checkpoint"]
        assert isinstance(checkpoint, dict)
        checkpoint_digest = checkpoint["checkpoint_digest"]
        assert isinstance(checkpoint_digest, str)
        current = await runtime.bus.dispatch(
            _rpc(
                "operation/resume",
                "current-resume",
                {
                    "project_id": "project:response-loss",
                    "operation_id": operation_id,
                    "expected_checkpoint_digest": checkpoint_digest,
                },
            )
        )
    finally:
        runtime.close()

    assert stale.error is not None
    assert stale.error.code == RpcErrorCode.STALE_CHECKPOINT
    assert current.result is not None
    current_value = current.result["value"]
    assert isinstance(current_value, dict)
    assert current_value["resume_allowed"] is False
    assert current_value["reason"] == "NO_METHOD_SPECIFIC_RESUME_HANDLER"
