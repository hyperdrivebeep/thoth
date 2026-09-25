from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

from thoth.apps.runtime import create_runtime
from thoth.protocol.jsonrpc import JsonRpcRequest, JsonRpcResponse
from thoth.protocol.registry import PUBLIC_METHODS


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
async def test_registry_operation_event_and_reopen_soak(tmp_path: Path) -> None:
    workspace = tmp_path / "soak"
    runtime = create_runtime(workspace)
    project_id = "project:full-soak"
    try:
        assert set(PUBLIC_METHODS) == set(runtime.bus.registered_methods())
        created_request = request(
            "project/create",
            "soak-create",
            {
                "project_id": project_id,
                "name": "Full backend soak",
                "cutoff_at": "2026-08-31T00:00:00Z",
            },
        )
        first = await runtime.bus.dispatch(created_request)
        replay = await runtime.bus.dispatch(created_request)
        assert first.model_dump(mode="json") == replay.model_dump(mode="json")
        for index in range(20):
            value(
                await runtime.bus.dispatch(
                    request(
                        "thread/start",
                        f"soak-thread-{index}",
                        {
                            "project_id": project_id,
                            "thread_id": f"thread:soak:{index}",
                            "problem": f"Bounded soak decision {index}",
                            "scope": {"workstream": f"ws-{index % 4}"},
                        },
                    )
                )
            )
        for index in range(200):
            result = value(
                await runtime.bus.dispatch(
                    request(
                        "project/read",
                        f"soak-read-{index}",
                        {"project_id": project_id},
                    )
                )
            )
            assert result["project_id"] == project_id
        threads = value(
            await runtime.bus.dispatch(
                request(
                    "thread/list",
                    "soak-thread-list",
                    {"project_id": project_id},
                )
            )
        )
        assert len(cast(list[object], threads["threads"])) == 20
        objects = value(
            await runtime.bus.dispatch(
                request(
                    "object/list",
                    "soak-object-list",
                    {"project_id": project_id},
                )
            )
        )
        assert len(cast(list[object], objects["objects"])) == 20
    finally:
        runtime.close()
    reopened = create_runtime(workspace)
    try:
        reread = value(
            await reopened.bus.dispatch(
                request(
                    "project/read",
                    "soak-reopen-read",
                    {"project_id": project_id},
                )
            )
        )
        assert reread["project_id"] == project_id
        assert (
            len(
                cast(
                    list[object],
                    value(
                        await reopened.bus.dispatch(
                            request(
                                "thread/list",
                                "soak-reopen-threads",
                                {"project_id": project_id},
                            )
                        )
                    )["threads"],
                )
            )
            == 20
        )
    finally:
        reopened.close()
