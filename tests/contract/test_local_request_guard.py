"""Local HTTP guard runs before parsing, runtime creation and file staging."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import cast

import httpx
import pytest
from fastapi import Request, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from thoth.adapters.http.app import create_app


@pytest.fixture
def local_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", "LOCAL")
    monkeypatch.setenv("THOTH_WORKSPACE", str(workspace))
    monkeypatch.setenv("THOTH_WEB_DIST", str(tmp_path / "missing-dist"))
    return workspace


@pytest.mark.parametrize(
    "method,path,headers,code",
    (
        ("GET", "/healthz", {"host": "evil.example"}, "LOCAL_HOST_REJECTED"),
        ("GET", "/", {"host": "evil.example"}, "LOCAL_HOST_REJECTED"),
        ("POST", "/rpc", {"origin": "null"}, "LOCAL_ORIGIN_REJECTED"),
        ("POST", "/rpc/query", {"content-type": "text/plain"}, "JSON_CONTENT_TYPE_REQUIRED"),
        ("POST", "/rpc/async", {}, "JSON_CONTENT_TYPE_REQUIRED"),
        (
            "POST",
            "/operations/one/cancel",
            {"origin": "https://localhost"},
            "LOCAL_ORIGIN_REJECTED",
        ),
        (
            "POST",
            "/files/stage",
            {"origin": "http://evil.example", "x-thoth-project-id": "p"},
            "LOCAL_ORIGIN_REJECTED",
        ),
        ("PUT", "/future-write", {"origin": "null"}, "LOCAL_ORIGIN_REJECTED"),
        ("PATCH", "/future-write", {"origin": "null"}, "LOCAL_ORIGIN_REJECTED"),
        ("DELETE", "/future-write", {"origin": "null"}, "LOCAL_ORIGIN_REJECTED"),
    ),
)
def test_rejection_does_not_parse_or_open_workspace(
    local_environment: Path,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    headers: dict[str, str],
    code: str,
) -> None:
    module = importlib.import_module("thoth.adapters.http.app")

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("request reached body/runtime/staging")

    monkeypatch.setattr(Request, "body", forbidden)
    monkeypatch.setattr(module, "_resolve_bus_for_request", forbidden)
    monkeypatch.setattr(module, "_stage_file", forbidden)
    app = create_app()
    with TestClient(app, base_url="http://localhost:8765") as client:
        response = cast(httpx.Client, client).request(method, path, headers=headers, content=b"{")
        assert response.status_code == (415 if code == "JSON_CONTENT_TYPE_REQUIRED" else 403)
        assert response.json()["code"] == code
        assert set(response.json()) == {"code", "message_ko", "message_en"}
        assert "evil.example" not in response.text
        assert not any(key.startswith("access-control-") for key in response.headers)
        assert app.state.runtime is None and app.state.bus is None
        assert app.state.background_tasks == {}
    assert not local_environment.exists()


@pytest.mark.parametrize("headers", ({"host": "evil.example"}, {"origin": "null"}))
def test_future_websocket_is_rejected_before_handler(
    local_environment: Path, headers: dict[str, str]
) -> None:
    app = create_app()

    async def endpoint(socket: WebSocket) -> None:
        pytest.fail("rejected websocket reached handler")

    app.add_api_websocket_route("/future-socket", endpoint)
    with (
        TestClient(app, base_url="http://localhost:8765") as client,
        pytest.raises(WebSocketDisconnect) as caught,
        client.websocket_connect("ws://localhost:8765/future-socket", headers=headers),
    ):
        pytest.fail("rejected websocket accepted")
    assert caught.value.code == 1008
    payload = json.loads(caught.value.reason)
    assert payload["code"] == (
        "LOCAL_HOST_REJECTED" if "host" in headers else "LOCAL_ORIGIN_REJECTED"
    )
    assert not local_environment.exists()


def test_future_websocket_without_origin_is_allowed(local_environment: Path) -> None:
    app = create_app()

    async def endpoint(socket: WebSocket) -> None:
        await socket.accept()
        await socket.send_text("synthetic")
        await socket.close()

    app.add_api_websocket_route("/future-socket", endpoint)
    with (
        TestClient(app, base_url="http://localhost:8765") as client,
        client.websocket_connect("ws://localhost:8765/future-socket") as socket,
    ):
        assert socket.receive_text() == "synthetic"
    assert not local_environment.exists()
