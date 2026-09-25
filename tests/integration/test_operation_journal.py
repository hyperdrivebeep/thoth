from __future__ import annotations

from pathlib import Path

import pytest

from thoth.apps.runtime import create_runtime
from thoth.protocol.jsonrpc import JsonRpcRequest


def _rpc(method: str, key: str, value: dict[str, object]) -> JsonRpcRequest:
    return JsonRpcRequest.model_validate(
        {
            "id": key,
            "method": method,
            "params": {"_meta": {"idempotencyKey": key}, "input": value},
        }
    )


@pytest.mark.asyncio
async def test_operation_events_are_hash_chained_and_checkpointed(tmp_path: Path) -> None:
    runtime = create_runtime(tmp_path / "workspace")
    try:
        created = await runtime.bus.dispatch(
            _rpc(
                "project/create",
                "journal-project",
                {
                    "project_id": "project:journal",
                    "name": "Journal",
                    "cutoff_at": "2026-08-30T12:00:00Z",
                },
            )
        )
        assert created.result is not None
        operation_id = created.result["operation_id"]
        assert isinstance(operation_id, str)
        read = await runtime.bus.dispatch(
            _rpc(
                "operation/read",
                "journal-read",
                {"project_id": "project:journal", "operation_id": operation_id},
            )
        )
        checkpoint_read = await runtime.bus.dispatch(
            _rpc(
                "operation/checkpoint/read",
                "journal-checkpoint-read",
                {"project_id": "project:journal", "operation_id": operation_id},
            )
        )
        pause = await runtime.bus.dispatch(
            _rpc(
                "operation/pause",
                "journal-pause",
                {"project_id": "project:journal", "operation_id": operation_id},
            )
        )
    finally:
        runtime.close()

    assert read.result is not None
    value = read.result["value"]
    assert isinstance(value, dict)
    events = value["events"]
    assert isinstance(events, list)
    assert [event["event_type"] for event in events if isinstance(event, dict)] == [
        "operation.started",
        "project/created",
        "operation.succeeded",
    ]
    first, domain_event, second = events
    assert isinstance(first, dict)
    assert isinstance(domain_event, dict)
    assert isinstance(second, dict)
    assert domain_event["previous_event_digest"] == first["event_digest"]
    assert second["previous_event_digest"] == domain_event["event_digest"]
    checkpoint = value["latest_checkpoint"]
    assert isinstance(checkpoint, dict)
    checkpoint_payload = checkpoint["payload"]
    assert isinstance(checkpoint_payload, dict)
    second_payload = second["payload"]
    assert isinstance(second_payload, dict)
    assert checkpoint_payload["result_digest"] == second_payload["result_digest"]
    assert checkpoint_read.result is not None
    checkpoint_value = checkpoint_read.result["value"]
    assert isinstance(checkpoint_value, dict)
    assert isinstance(checkpoint_value["checkpoint"], dict)
    assert pause.result is not None
    pause_value = pause.result["value"]
    assert isinstance(pause_value, dict)
    assert pause_value["pause_accepted"] is False
    assert pause_value["reason"] == "NO_METHOD_SPECIFIC_PAUSE_HANDLER"
