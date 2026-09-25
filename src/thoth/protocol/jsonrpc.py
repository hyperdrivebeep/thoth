from __future__ import annotations

from enum import IntEnum
from typing import Literal

from pydantic import Field, JsonValue, model_validator

from thoth.domain.base import DomainModel


class RpcErrorCode(IntEnum):
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603
    IDEMPOTENCY_CONFLICT = -32010
    PROJECT_NOT_FOUND = -32020
    PROJECT_ALREADY_EXISTS = -32021
    OPERATION_NOT_FOUND = -32022
    THREAD_NOT_FOUND = -32023
    THREAD_ALREADY_EXISTS = -32024
    DOMAIN_REJECTED = -32030
    STALE_CHECKPOINT = -32031
    AUTHORIZATION_DENIED = -32040


class RpcMeta(DomainModel):
    idempotency_key: str = Field(alias="idempotencyKey", min_length=1, max_length=260)
    expected_head_digest: str | None = Field(default=None, alias="expectedHeadDigest")
    progress_token: str | None = Field(default=None, alias="progressToken")
    data_scope: dict[str, str] = Field(default_factory=dict, alias="dataScope")
    field_session_id: str | None = Field(default=None, alias="fieldSessionId", max_length=200)


class RpcParams(DomainModel):
    meta: RpcMeta = Field(alias="_meta")
    input: dict[str, JsonValue]


class JsonRpcRequest(DomainModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: str | int
    method: str = Field(min_length=1, max_length=160)
    params: RpcParams


class JsonRpcError(DomainModel):
    code: int
    message: str
    data: dict[str, JsonValue] = Field(default_factory=dict)


class JsonRpcResponse(DomainModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: str | int | None
    result: dict[str, JsonValue] | None = None
    error: JsonRpcError | None = None

    @model_validator(mode="after")
    def exactly_one_payload(self) -> JsonRpcResponse:
        if (self.result is None) == (self.error is None):
            raise ValueError("JSON-RPC response requires exactly one of result or error")
        return self


class RpcApplicationError(Exception):
    def __init__(
        self,
        code: RpcErrorCode,
        message: str,
        *,
        data: dict[str, JsonValue] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = {} if data is None else data
