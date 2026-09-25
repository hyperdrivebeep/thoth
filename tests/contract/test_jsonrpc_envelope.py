from __future__ import annotations

from typing import Any

import pytest

from thoth.protocol.bus import CommandBus
from thoth.protocol.jsonrpc import JsonRpcRequest, RpcErrorCode


def _create_request(*, request_id: str = "req-1", name: str = "센서 과제") -> JsonRpcRequest:
    return JsonRpcRequest.model_validate(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "project/create",
            "params": {
                "_meta": {"idempotencyKey": "create-project-demo"},
                "input": {
                    "project_id": "project:demo",
                    "name": name,
                    "cutoff_at": "2026-08-30T06:40:00Z",
                    "overlay": "general-rnd",
                    "policy_binding_ref": "policy:default",
                },
            },
        }
    )


@pytest.mark.asyncio
async def test_duplicate_idempotency_returns_same_operation_and_result(
    command_bus: CommandBus,
) -> None:
    first = await command_bus.dispatch(_create_request())
    second = await command_bus.dispatch(_create_request())

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.result is not None
    assert first.result["operation_id"] == "operation:1"
    assert first.result["state"] == "SUCCEEDED"
    value = first.result["value"]
    assert isinstance(value, dict)
    assert value["project_id"] == "project:demo"
    assert value["name"] == "센서 과제"
    assert value["cutoff_at"] == "2026-08-30T06:40:00Z"
    assert value["lifecycle"] == "DRAFT"
    assert value["overlay"] == "general-rnd"
    assert value["policy_binding_ref"] == "policy:project:demo:v1"
    assert value["revision"] == 0
    assert value["roles"] == []
    assert value["source_bindings"] == []
    assert isinstance(value["policy"], dict)


@pytest.mark.asyncio
async def test_same_idempotency_key_with_different_scope_conflicts(command_bus: CommandBus) -> None:
    await command_bus.dispatch(_create_request())
    conflict = await command_bus.dispatch(_create_request(name="다른 이름"))

    assert conflict.error is not None
    assert conflict.error.code == RpcErrorCode.IDEMPOTENCY_CONFLICT
    assert conflict.error.data["existingOperationId"] == "operation:1"


@pytest.mark.asyncio
async def test_invalid_params_are_typed_and_cached(command_bus: CommandBus) -> None:
    payload: dict[str, Any] = _create_request().model_dump(mode="json", by_alias=True)
    del payload["params"]["input"]["cutoff_at"]
    request = JsonRpcRequest.model_validate(payload)

    response = await command_bus.dispatch(request)

    assert response.error is not None
    assert response.error.code == RpcErrorCode.INVALID_PARAMS


@pytest.mark.asyncio
async def test_project_and_operation_can_be_read_through_registered_methods(
    command_bus: CommandBus,
) -> None:
    created = await command_bus.dispatch(_create_request())
    assert created.result is not None
    operation_id = created.result["operation_id"]
    assert isinstance(operation_id, str)

    project_read = JsonRpcRequest.model_validate(
        {
            "id": "req-read-project",
            "method": "project/read",
            "params": {
                "_meta": {"idempotencyKey": "read-project-demo"},
                "input": {"project_id": "project:demo"},
            },
        }
    )
    operation_read = JsonRpcRequest.model_validate(
        {
            "id": "req-read-operation",
            "method": "operation/read",
            "params": {
                "_meta": {"idempotencyKey": "read-operation-demo"},
                "input": {
                    "project_id": "project:demo",
                    "operation_id": operation_id,
                },
            },
        }
    )

    project_response = await command_bus.dispatch(project_read)
    operation_response = await command_bus.dispatch(operation_read)

    assert project_response.result is not None
    project_value = project_response.result["value"]
    assert isinstance(project_value, dict)
    assert project_value["name"] == "센서 과제"
    assert operation_response.result is not None
    operation_value = operation_response.result["value"]
    assert isinstance(operation_value, dict)
    assert operation_value["operation_id"] == operation_id
