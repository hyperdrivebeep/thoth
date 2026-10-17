"""Freeze internal HTTP authorization, read order and lifecycle before extraction."""

import importlib
from pathlib import Path
from types import FunctionType, SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from thoth.apps.hosted_review_dispatch import SNAPSHOT_UNCOMMITTED, hosted_dispatch
from thoth.domain.operation import OperationState
from thoth.protocol.jsonrpc import RpcErrorCode

http_app = importlib.import_module("thoth.adapters.http.app")
ROUTES = (
    ("GET", "/internal/workspace-export"),
    ("POST", "/internal/workspace-restore"),
    ("POST", "/internal/dispatch-release"),
    ("POST", "/internal/dispatch-abort"),
)
SECRET = {"x-thoth-review-internal": "fixture-secret"}


@pytest.fixture
def application(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", "HOSTED_REVIEW")
    monkeypatch.setenv("THOTH_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("THOTH_WEB_DIST", str(tmp_path / "missing-dist"))
    monkeypatch.setenv("THOTH_REVIEW_SESSION_SECRET", "fixture-secret")
    monkeypatch.delenv("THOTH_REVIEW_SESSION_ID", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    return cast(FastAPI, http_app.create_app())


def endpoint_globals(application: FastAPI, path: str) -> dict[str, Any]:
    method = next(method for method, candidate in ROUTES if candidate == path)
    route = next(
        r
        for r in application.routes
        if isinstance(r, APIRoute) and r.path == path and method in (r.methods or set())
    )
    return cast(FunctionType, route.endpoint).__globals__


def body_spy(monkeypatch: pytest.MonkeyPatch, events: list[str]) -> None:
    original = Request.body

    async def body(request: Request) -> bytes:
        events.append("body")
        return await original(request)

    monkeypatch.setattr(Request, "body", body)


@pytest.mark.parametrize(("method", "path"), ROUTES)
@pytest.mark.parametrize("mode", ("LOCAL", "HOSTED_REVIEW"))
def test_denied_internal_routes_never_read_body_or_workspace(
    application: FastAPI, monkeypatch: pytest.MonkeyPatch, mode: str, method: str, path: str
) -> None:
    monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", mode)

    async def forbidden_body(_request: Request) -> bytes:
        pytest.fail("unauthorized body read")

    def forbidden_workspace() -> Path:
        pytest.fail("unauthorized workspace access")

    monkeypatch.setattr(Request, "body", forbidden_body)
    monkeypatch.setattr(http_app, "_workspace_path", forbidden_workspace)
    headers = SECRET if mode == "LOCAL" else {}
    response = cast(httpx.Client, TestClient(application)).request(
        method, path, headers=headers, content=b"{"
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "not found"}
    assert application.state.runtime is None and application.state.bus is None


@pytest.mark.parametrize("remote", (False, True))
def test_pinned_session_requires_loopback_and_export_preserves_headers(
    application: FastAPI, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, remote: bool
) -> None:
    monkeypatch.setenv("THOTH_REVIEW_SESSION_ID", "pinned")
    events: list[str] = []
    selected = tmp_path / "changed-after-mount"

    def workspace() -> Path:
        events.append("workspace")
        return selected

    def export(path: Path) -> bytes:
        assert path == selected
        events.append("export")
        return b"synthetic archive"

    def database(path: Path) -> Path:
        assert path == selected
        events.append("sqlite_path")
        return path / "state.db"

    def running(path: Path) -> bool:
        assert path == selected / "state.db"
        events.append("running")
        return True

    def count(path: Path) -> int:
        assert path == selected
        events.append("count")
        return 3

    monkeypatch.setattr(http_app, "_workspace_path", workspace)
    globals_ = endpoint_globals(application, "/internal/workspace-export")
    for name, function in (
        ("export_workspace_archive", export),
        ("sqlite_path", database),
        ("sqlite_has_running", running),
        ("snapshot_file_count", count),
    ):
        monkeypatch.setitem(globals_, name, function)
    client = cast(
        httpx.Client,
        TestClient(application, client=("203.0.113.20" if remote else "testclient", 50100)),
    )
    response = client.get(
        "/internal/workspace-export", headers={"x-thoth-review-session": "pinned"}
    )
    if remote:
        assert response.status_code == 404 and events == []
    else:
        assert response.status_code == 200 and response.content == b"synthetic archive"
        assert response.headers["content-type"] == "application/gzip"
        assert response.headers["x-thoth-research-running"] == "1"
        assert response.headers["x-thoth-file-count"] == "3"
        assert events == ["workspace", "export", "sqlite_path", "running", "count"]


@pytest.mark.parametrize("outcome", ("tasks", "live", "success", "invalid"))
def test_restore_keeps_skip_close_state_body_and_mark_order(
    application: FastAPI, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, outcome: str
) -> None:
    events: list[str] = []
    body_spy(monkeypatch, events)
    sentinel = object()

    class Runtime:
        def close(self) -> None:
            assert application.state.bus is sentinel
            events.append("close")

    runtime = Runtime()
    application.state.runtime = runtime
    application.state.bus = sentinel
    if outcome == "tasks":
        application.state.background_tasks["running"] = object()

    def live(path: Path) -> bool:
        assert path == tmp_path
        events.append("live")
        return outcome == "live"

    def restore(path: Path, payload: bytes) -> dict[str, int]:
        assert path == tmp_path and payload == b"archive bytes"
        assert application.state.runtime is None and application.state.bus is None
        events.append("restore")
        if outcome == "invalid":
            raise ValueError("bad archive")
        return {"restored_members": 2}

    def mark(path: Path) -> None:
        assert path == tmp_path
        events.append("mark")

    globals_ = endpoint_globals(application, "/internal/workspace-restore")
    for name, function in (
        ("workspace_is_live", live),
        ("restore_workspace_archive", restore),
        ("mark_workspace_live", mark),
    ):
        monkeypatch.setitem(globals_, name, function)
    response = cast(httpx.Client, TestClient(application)).post(
        "/internal/workspace-restore", headers=SECRET, content=b"archive bytes"
    )
    if outcome in ("tasks", "live"):
        assert response.status_code == 200
        assert response.json() == {"skipped": True, "reason": "live-runtime"}
        assert events == ([] if outcome == "tasks" else ["live"])
        assert application.state.runtime is runtime and application.state.bus is sentinel
    else:
        assert application.state.runtime is None and application.state.bus is None
        if outcome == "success":
            assert response.status_code == 200 and response.json() == {"restored_members": 2}
            assert events == ["live", "close", "body", "restore", "mark"]
        else:
            assert response.status_code == 503 and response.json() == {"detail": "bad archive"}
            assert events == ["live", "close", "body", "restore"]


@pytest.mark.parametrize("path", ("/internal/dispatch-release", "/internal/dispatch-abort"))
@pytest.mark.parametrize(
    "payload",
    (b"{", b"[]", b"{}", b'{"operation_id":""}', b'{"operation_id":42}', b'{"operation_id":"  "}'),
)
def test_invalid_dispatch_input_is_rejected_before_bus_access(
    application: FastAPI, monkeypatch: pytest.MonkeyPatch, path: str, payload: bytes
) -> None:
    def no_bus(*_args: object) -> object:
        pytest.fail("invalid input reached bus")

    monkeypatch.setattr(http_app, "_resolve_bus_for_request", no_bus)
    response = cast(httpx.Client, TestClient(application)).post(
        path, headers={**SECRET, "x-thoth-review-session": "s"}, content=payload
    )
    assert response.status_code == 400
    assert response.json() == {
        "detail": "invalid JSON" if payload == b"{" else "operation_id is required"
    }


def test_abort_missing_session_is_rejected_before_body(
    application: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def forbidden(_request: Request) -> bytes:
        pytest.fail("sessionless abort read body")

    monkeypatch.setattr(Request, "body", forbidden)
    response = cast(httpx.Client, TestClient(application)).post(
        "/internal/dispatch-abort", headers=SECRET, content=b"{"
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "x-thoth-review-session is required"}


def test_release_passes_original_id_and_trims_only_response(
    application: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def release(_gate: object, identifier: str) -> None:
        calls.append(identifier)

    monkeypatch.setattr(type(hosted_dispatch), "release", release)
    response = cast(httpx.Client, TestClient(application)).post(
        "/internal/dispatch-release", headers=SECRET, json={"operation_id": " op-1 "}
    )
    assert response.content == b'{"released":"op-1"}' and response.status_code == 200
    assert calls == [" op-1 "]


@pytest.mark.parametrize("pinned", (False, True))
@pytest.mark.parametrize("found", (False, True))
def test_abort_preserves_session_bus_and_failure_payload(
    application: FastAPI, monkeypatch: pytest.MonkeyPatch, found: bool, pinned: bool
) -> None:
    events: list[str] = []
    failures: list[dict[str, object]] = []
    if pinned:
        monkeypatch.setenv("THOTH_REVIEW_SESSION_ID", "s")

    class Bus:
        def fail_if_running(self, identifier: str, error: dict[str, object]) -> object:
            assert identifier == "op-1"
            events.append("fail")
            failures.append(error)
            return SimpleNamespace(state=OperationState.FAILED) if found else None

    def resolve(app: FastAPI, request: Request) -> Bus:
        assert app is application
        assert http_app.hosted_request_session(request) == "s"
        events.append("resolve")
        return Bus()

    def abort(_gate: object, identifier: str) -> None:
        assert identifier == "op-1"
        events.append("abort")

    monkeypatch.setattr(http_app, "_resolve_bus_for_request", resolve)
    monkeypatch.setattr(type(hosted_dispatch), "abort", abort)
    response = cast(httpx.Client, TestClient(application)).post(
        "/internal/dispatch-abort",
        headers={**SECRET, **({} if pinned else {"x-thoth-review-session": "s"})},
        json={"operation_id": " op-1 "},
    )
    assert response.status_code == 200
    assert response.json() == {"aborted": "op-1", "state": "FAILED" if found else None}
    assert events == ["abort", "resolve", "fail"]
    assert failures == [
        {
            "code": int(RpcErrorCode.DOMAIN_REJECTED),
            "message": SNAPSHOT_UNCOMMITTED,
            "data": {
                "reason_code": SNAPSHOT_UNCOMMITTED,
                "remote_observation": "NOT_SENT",
                "pre_io": True,
            },
        }
    ]
