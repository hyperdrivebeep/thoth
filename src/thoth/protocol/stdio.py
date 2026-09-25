from __future__ import annotations

from typing import cast

import orjson
from pydantic import JsonValue, ValidationError

from thoth.protocol.bus import CommandBus
from thoth.protocol.jsonrpc import (
    JsonRpcError,
    JsonRpcRequest,
    JsonRpcResponse,
    RpcErrorCode,
)


def encode_response(response: JsonRpcResponse) -> bytes:
    payload = response.model_dump(mode="json", by_alias=True)
    if response.result is None:
        payload.pop("result", None)
    if response.error is None:
        payload.pop("error", None)
    return orjson.dumps(
        payload,
        option=orjson.OPT_SORT_KEYS,
    )


async def handle_json_line(line: bytes, bus: CommandBus) -> bytes:
    try:
        payload = orjson.loads(line)
    except orjson.JSONDecodeError:
        return encode_response(
            JsonRpcResponse(
                id=None,
                error=JsonRpcError(code=RpcErrorCode.PARSE_ERROR, message="invalid JSON"),
            )
        )
    try:
        request = JsonRpcRequest.model_validate(payload)
    except ValidationError as exc:
        return encode_response(
            JsonRpcResponse(
                id=None,
                error=JsonRpcError(
                    code=RpcErrorCode.INVALID_REQUEST,
                    message="invalid JSON-RPC request",
                    data={
                        "validation": cast(
                            JsonValue,
                            orjson.loads(exc.json(include_url=False)),
                        )
                    },
                ),
            )
        )
    return encode_response(await bus.dispatch(request))
