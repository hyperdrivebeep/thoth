from __future__ import annotations

from typing import cast

import httpx
import orjson
import pytest
from apps.api.main import create_app

from thoth.protocol.bus import CommandBus
from thoth.protocol.stdio import handle_json_line


def _request() -> bytes:
    return orjson.dumps(
        {
            "jsonrpc": "2.0",
            "id": "req-transport",
            "method": "project/create",
            "params": {
                "_meta": {"idempotencyKey": "transport-equivalence"},
                "input": {
                    "project_id": "project:transport",
                    "name": "전송 계약",
                    "cutoff_at": "2026-08-30T06:40:00Z",
                },
            },
        }
    )


@pytest.mark.asyncio
async def test_stdio_and_http_return_same_payload(command_bus: CommandBus) -> None:
    request = _request()
    stdio_payload = cast(
        dict[str, object], orjson.loads(await handle_json_line(request, command_bus))
    )

    transport = httpx.ASGITransport(app=create_app(command_bus))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        http_response = await client.post(
            "/rpc", content=request, headers={"content-type": "application/json"}
        )

    assert http_response.status_code == 200
    assert http_response.json() == stdio_payload


@pytest.mark.asyncio
async def test_parse_error_is_a_jsonrpc_error(command_bus: CommandBus) -> None:
    payload = cast(
        dict[str, object], orjson.loads(await handle_json_line(b"not-json", command_bus))
    )

    assert payload == {
        "error": {"code": -32700, "data": {}, "message": "invalid JSON"},
        "id": None,
        "jsonrpc": "2.0",
    }
