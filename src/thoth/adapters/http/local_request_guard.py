"""Reject nonlocal request origins before body, runtime or storage access."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from thoth.adapters.http.hosted_review import hosted_mode

_NAMES = frozenset({"127.0.0.1", "localhost", "::1"})
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_JSON_PATHS = frozenset({"/rpc", "/rpc/query", "/rpc/async"})
_CANCEL_PATH = re.compile(r"/operations/[^/]+/cancel")


@dataclass(frozen=True)
class LocalRequestRejection:
    status: int
    code: Literal["LOCAL_HOST_REJECTED", "LOCAL_ORIGIN_REJECTED", "JSON_CONTENT_TYPE_REQUIRED"]
    message_ko: str
    message_en: str

    def payload(self) -> dict[str, str]:
        return {"code": self.code, "message_ko": self.message_ko, "message_en": self.message_en}


_HOST = LocalRequestRejection(403, "LOCAL_HOST_REJECTED", "호스트 거부", "Host rejected")
_ORIGIN = LocalRequestRejection(403, "LOCAL_ORIGIN_REJECTED", "출처 거부", "Origin rejected")
_JSON = LocalRequestRejection(415, "JSON_CONTENT_TYPE_REQUIRED", "JSON 필요", "JSON required")


def _header_values(headers: Sequence[tuple[bytes, bytes]], name: bytes) -> list[str]:
    return [value.decode("latin-1") for key, value in headers if key.lower() == name]


def _local_authority(value: str, *, origin: bool) -> bool:
    if not value or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        return False
    try:
        parsed = urlsplit(value if origin else "//" + value)
        if origin and parsed.scheme.lower() != "http":
            return False
        if (
            parsed.hostname not in _NAMES
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or "?" in value
            or "#" in value
            or "\\" in value
        ):
            return False
        # Validate port syntax without restricting the server or proxy's port.
        _ = parsed.port
        authority = parsed.netloc
        if authority.endswith(":"):
            return False
        return parsed.hostname != "::1" or authority == "[::1]" or authority.startswith("[::1]:")
    except ValueError:
        return False


def check_local_request(
    method: str,
    path: str,
    headers: Sequence[tuple[bytes, bytes]],
    *,
    websocket: bool = False,
) -> LocalRequestRejection | None:
    hosts = _header_values(headers, b"host")
    if len(hosts) != 1 or not _local_authority(hosts[0], origin=False):
        return _HOST
    if not websocket and method.upper() in _SAFE_METHODS:
        return None
    origins = _header_values(headers, b"origin")
    if origins and (len(origins) != 1 or not _local_authority(origins[0], origin=True)):
        return _ORIGIN
    normalized = path.rstrip("/")
    if not websocket and (normalized in _JSON_PATHS or _CANCEL_PATH.fullmatch(normalized)):
        types = _header_values(headers, b"content-type")
        if len(types) != 1 or types[0].split(";", 1)[0].strip().lower() != "application/json":
            return _JSON
    return None


class LocalRequestGuard:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope["type"]
        if kind not in {"http", "websocket"} or hosted_mode():
            await self.app(scope, receive, send)
            return
        rejection = check_local_request(
            scope.get("method", "GET"),
            scope["path"],
            scope["headers"],
            websocket=kind == "websocket",
        )
        if rejection is None:
            await self.app(scope, receive, send)
        elif kind == "websocket":
            await send(
                {
                    "type": "websocket.close",
                    "code": 1008,
                    "reason": json.dumps(
                        rejection.payload(), ensure_ascii=False, separators=(",", ":")
                    ),
                }
            )
        else:
            await JSONResponse(rejection.payload(), status_code=rejection.status)(
                scope, receive, send
            )
