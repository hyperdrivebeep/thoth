"""Exact local authority matching and rejection before any ASGI body consumption."""

from __future__ import annotations

import json

import pytest
from starlette.types import Message, Receive, Scope, Send

from thoth.adapters.http.local_request_guard import LocalRequestGuard, check_local_request


def headers(
    host: str = "localhost", origin: str | None = None, media: str = "application/json"
) -> list[tuple[bytes, bytes]]:
    values = [(b"host", host.encode("latin-1")), (b"content-type", media.encode())]
    if origin is not None:
        values.append((b"origin", origin.encode("latin-1")))
    return values


@pytest.mark.parametrize(
    "host", ("localhost", "LOCALHOST:5173", "127.0.0.1", "127.0.0.1:8765", "[::1]", "[::1]:65535")
)
def test_allowed_host_names_and_arbitrary_valid_ports(host: str) -> None:
    assert check_local_request("POST", "/rpc", headers(host)) is None


@pytest.mark.parametrize(
    "host",
    (
        "",
        "testserver",
        "localhost.evil.example",
        "127.0.0.1.evil.example",
        "::1",
        "[0:0:0:0:0:0:0:1]",
        "[::2]",
        "[::1]evil",
        "[::1]evil:80",
        "localhost:",
        "localhost:bad",
        "localhost:65536",
        "localhost/path",
        "localhost?",
        "localhost#",
        "evil@localhost",
        " localhost",
        "local\thost",
        "[::1",
        "localhost\\evil",
    ),
)
def test_unlisted_or_malformed_host_is_rejected(host: str) -> None:
    rejection = check_local_request("GET", "/healthz", headers(host))
    assert rejection is not None and rejection.code == "LOCAL_HOST_REJECTED"


@pytest.mark.parametrize(
    "origin",
    (
        None,
        "http://localhost",
        "HTTP://LOCALHOST:5173",
        "http://127.0.0.1:8765",
        "http://[::1]:5173",
    ),
)
def test_allowed_origins(origin: str | None) -> None:
    assert check_local_request("POST", "/rpc", headers(origin=origin)) is None


@pytest.mark.parametrize(
    "origin",
    (
        "",
        "null",
        "https://localhost",
        "http://localhost.evil.example",
        "http://127.0.0.1.evil.example",
        "http://evil@localhost",
        "http://localhost/",
        "http://localhost?q=x",
        "http://localhost#",
        "http://[::2]",
        "http://[::1]evil",
        "http://[::1]evil:80",
        "http://localhost:bad",
        "http://local\nhost",
    ),
)
def test_unlisted_or_malformed_origin_is_rejected(origin: str) -> None:
    rejection = check_local_request("POST", "/new-action", headers(origin=origin))
    assert rejection is not None and rejection.code == "LOCAL_ORIGIN_REJECTED"


@pytest.mark.parametrize(
    "path",
    (
        "/rpc",
        "/rpc/",
        "/rpc/query",
        "/rpc/async",
        "/operations/op:1/cancel",
        "/operations/op:1/cancel/",
    ),
)
@pytest.mark.parametrize(
    "media", ("application/json", "APPLICATION/JSON", "application/json; charset=utf-8")
)
def test_json_paths_accept_case_and_parameters(path: str, media: str) -> None:
    assert check_local_request("POST", path, headers(media=media)) is None


@pytest.mark.parametrize(
    "media",
    (
        "",
        "text/plain",
        "application/x-www-form-urlencoded",
        "application/jsonp",
        "application/ld+json",
    ),
)
def test_non_json_media_rejected(media: str) -> None:
    rejection = check_local_request("POST", "/rpc", headers(media=media))
    assert rejection is not None and rejection.status == 415


@pytest.mark.parametrize(
    "name,code",
    (
        (b"host", "LOCAL_HOST_REJECTED"),
        (b"origin", "LOCAL_ORIGIN_REJECTED"),
        (b"content-type", "JSON_CONTENT_TYPE_REQUIRED"),
    ),
)
def test_duplicate_or_missing_required_headers(name: bytes, code: str) -> None:
    values = headers(origin="http://localhost")
    duplicate = next(item for item in values if item[0] == name)
    rejected = check_local_request("POST", "/rpc", [*values, duplicate])
    assert rejected is not None and rejected.code == code
    if name != b"origin":
        rejected = check_local_request("POST", "/rpc", [item for item in values if item[0] != name])
        assert rejected is not None and rejected.code == code


def test_rejection_order_and_fixed_bilingual_payload() -> None:
    for host, origin, code in (
        ("evil.example", "null", "LOCAL_HOST_REJECTED"),
        ("localhost", "null", "LOCAL_ORIGIN_REJECTED"),
        ("localhost", None, "JSON_CONTENT_TYPE_REQUIRED"),
    ):
        rejected = check_local_request("POST", "/rpc", headers(host, origin, "text/plain"))
        assert rejected is not None and rejected.code == code
        payload = rejected.payload()
        assert set(payload) == {"code", "message_ko", "message_en"}
        assert payload["message_ko"] and payload["message_en"]
        assert "evil.example" not in json.dumps(payload)


@pytest.mark.parametrize("method", ("GET", "HEAD", "OPTIONS"))
def test_safe_methods_only_check_host(method: str) -> None:
    assert check_local_request(method, "/rpc", headers(origin="null", media="")) is None


@pytest.mark.parametrize("method", ("POST", "PUT", "PATCH", "DELETE", "CUSTOM"))
def test_future_mutating_routes_automatically_check_origin(method: str) -> None:
    rejected = check_local_request(method, "/future-route", headers(origin="null"))
    assert rejected is not None and rejected.code == "LOCAL_ORIGIN_REJECTED"
    assert check_local_request(method, "/files/stage", headers(media="multipart/form-data")) is None


@pytest.mark.parametrize("kind", ("http", "websocket"))
async def test_guard_rejects_before_body_or_downstream(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", "LOCAL")
    sent: list[Message] = []

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        pytest.fail("rejected request reached downstream")

    async def receive() -> Message:
        pytest.fail("rejected request read body")

    async def send(message: Message) -> None:
        sent.append(message)

    scope: Scope = {
        "type": kind,
        "method": "POST",
        "path": "/rpc",
        "headers": headers(origin="null"),
    }
    await LocalRequestGuard(downstream)(scope, receive, send)
    if kind == "http":
        assert sent[0]["status"] == 403
        assert not any(k.startswith(b"access-control-") for k, _ in sent[0]["headers"])
        payload = json.loads(sent[1]["body"])
    else:
        assert sent[0]["type"] == "websocket.close" and sent[0]["code"] == 1008
        assert len(sent[0]["reason"].encode()) <= 123
        payload = json.loads(sent[0]["reason"])
    assert payload["code"] == "LOCAL_ORIGIN_REJECTED"


@pytest.mark.parametrize("kind,hosted", (("lifespan", False), ("http", True), ("websocket", True)))
async def test_other_scopes_and_hosted_pass_through(
    monkeypatch: pytest.MonkeyPatch, kind: str, hosted: bool
) -> None:
    monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", "HOSTED_REVIEW" if hosted else "LOCAL")
    reached: list[Scope] = []

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        reached.append(scope)

    async def receive() -> Message:
        pytest.fail("unexpected receive")

    async def send(message: Message) -> None:
        pytest.fail("unexpected rejection")

    scope: Scope = {"type": kind, "path": "/rpc", "headers": []}
    await LocalRequestGuard(downstream)(scope, receive, send)
    assert reached == [scope]


def test_websocket_checks_origin_even_on_get_but_does_not_require_json() -> None:
    assert check_local_request("GET", "/socket", headers(media=""), websocket=True) is None
    rejected = check_local_request("GET", "/socket", headers(origin="null"), websocket=True)
    assert rejected is not None and rejected.code == "LOCAL_ORIGIN_REJECTED"
