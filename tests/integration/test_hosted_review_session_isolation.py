from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Protocol, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.integration.storage_coverage_helpers import request

from thoth.adapters.http.app import create_app
from thoth.apps.runtime import create_runtime
from thoth.apps.runtime_types import AppRuntime, RuntimeWithLedger
from thoth.domain.deployment_mode import DeploymentMode
from thoth.protocol.bus import CommandBus
from thoth.protocol.jsonrpc import JsonRpcResponse


class _HostedAppState(Protocol):
    bus: CommandBus | None
    session_runtimes: dict[str, AppRuntime]
    session_last_seen: dict[str, float]


def _record(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    assert all(isinstance(key, str) for key in cast(dict[object, object], value))
    return cast(dict[str, object], value)


def _list(value: object) -> list[object]:
    assert isinstance(value, list)
    return cast(list[object], value)


def _text(value: object) -> str:
    assert isinstance(value, str)
    return value


def _rpc_value(response: JsonRpcResponse) -> dict[str, object]:
    assert response.error is None, response.error
    assert response.result is not None
    return _record(response.result["value"])


def _hosted_state(app: FastAPI) -> _HostedAppState:
    state: object = app.state
    bus: object = app.state.bus
    assert bus is None or isinstance(bus, CommandBus)
    runtimes: object = app.state.session_runtimes
    assert isinstance(runtimes, dict)
    assert all(
        isinstance(key, str) and isinstance(runtime, RuntimeWithLedger)
        for key, runtime in cast(dict[object, object], runtimes).items()
    )
    last_seen: object = app.state.session_last_seen
    assert isinstance(last_seen, dict)
    assert all(
        isinstance(key, str) and type(seen) in (int, float)
        for key, seen in cast(dict[object, object], last_seen).items()
    )
    return cast(_HostedAppState, state)


@pytest.mark.asyncio
async def test_session_workspaces_do_not_share_grant_or_inbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", DeploymentMode.HOSTED_REVIEW.value)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    workspace_a = tmp_path / "a"
    workspace_b = tmp_path / "b"
    runtime_a = create_runtime(workspace_a, deployment_mode=DeploymentMode.HOSTED_REVIEW)
    runtime_b = create_runtime(workspace_b, deployment_mode=DeploymentMode.HOSTED_REVIEW)
    try:
        await runtime_a.bus.dispatch(
            request("workspace/setup/update", "a-allow", {"internet_consent": "ALLOWED"})
        )
        inbox = workspace_a / "inbox" / "private.txt"
        inbox.parent.mkdir(parents=True, exist_ok=True)
        inbox.write_text("session-a-only", encoding="utf-8")
        ready_a = _rpc_value(await runtime_a.bus.query(request("workspace/ready", "a-ready", {})))
        ready_b = _rpc_value(await runtime_b.bus.query(request("workspace/ready", "b-ready", {})))
        setup_a = _record(ready_a["setup"])
        setup_b = _record(ready_b["setup"])
        assert setup_a["internet_consent"] == "ALLOWED"
        assert setup_a["internet_grant_id"]
        assert setup_b["internet_consent"] == "UNDECIDED"
        assert setup_b.get("internet_grant_id") in {None, ""}
        assert not (workspace_b / "inbox" / "private.txt").exists()
        assert (
            (workspace_a / "inbox" / "private.txt").read_text(encoding="utf-8") == "session-a-only"
        )
    finally:
        runtime_a.close()
        runtime_b.close()


def _http_rpc(
    client: TestClient,
    *,
    session_id: str,
    method: str,
    request_id: str,
    input_value: dict[str, object],
) -> dict[str, object]:
    response = client.post(
        "/rpc/query" if method.endswith("/list") or method.endswith("/read") else "/rpc",
        headers={
            "x-thoth-review-internal": "internal-secret",
            "x-thoth-review-session": session_id,
        },
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": {
                "_meta": {"idempotencyKey": request_id},
                "input": input_value,
            },
        },
    )
    assert response.status_code == 200
    payload = JsonRpcResponse.model_validate(response.json(), strict=True)
    assert payload.error is None, payload.error
    assert payload.result is not None
    value_payload = _record(payload.result["value"])
    assert isinstance(value_payload, dict)
    return value_payload


def test_normal_http_sessions_do_not_share_project_list(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", DeploymentMode.HOSTED_REVIEW.value)
    monkeypatch.setenv("THOTH_WORKSPACE", str(tmp_path / "origin"))
    monkeypatch.setenv("THOTH_REVIEW_SESSION_SECRET", "internal-secret")
    monkeypatch.delenv("THOTH_REVIEW_SESSION_ID", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    app = create_app()
    with TestClient(app) as client:
        created = _http_rpc(
            client,
            session_id="session-a",
            method="project/create",
            request_id="a-create-project",
            input_value={
                "project_id": "project:session-a-only",
                "name": "Session A only",
                "cutoff_at": "2026-09-24T00:00:00Z",
            },
        )
        assert _text(created["project_id"]) == "project:session-a-only"

        visible_to_a = _http_rpc(
            client,
            session_id="session-a",
            method="project/list",
            request_id="a-list-projects",
            input_value={"project_id": "system:projects"},
        )
        visible_to_b = _http_rpc(
            client,
            session_id="session-b",
            method="project/list",
            request_id="b-list-projects",
            input_value={"project_id": "system:projects"},
        )

        assert [_text(_record(item)["project_id"]) for item in _list(visible_to_a["projects"])] == [
            "project:session-a-only"
        ]
        assert _list(visible_to_b["projects"]) == []

        staged = client.post(
            "/files/stage",
            headers={
                "x-thoth-review-internal": "internal-secret",
                "x-thoth-review-session": "session-a",
                "x-thoth-project-id": "project:session-a-only",
            },
            files={"file": ("private.txt", b"session-a-private", "text/plain")},
        )
        assert staged.status_code == 200, staged.text
        relative = _text(_record(staged.json())["relative_path"])
        base = tmp_path / "origin" / "hosted-review-sessions"
        session_a = base / f"session-a-{sha256(b'session-a').hexdigest()[:12]}"
        session_b = base / f"session-b-{sha256(b'session-b').hexdigest()[:12]}"
        assert (session_a / "inbox" / relative).read_bytes() == b"session-a-private"
        assert not (session_b / "inbox" / relative).exists()


def test_free_origin_requires_internal_secret_for_session_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", DeploymentMode.HOSTED_REVIEW.value)
    monkeypatch.setenv("THOTH_WORKSPACE", str(tmp_path / "origin"))
    monkeypatch.delenv("THOTH_REVIEW_SESSION_ID", raising=False)
    monkeypatch.delenv("THOTH_REVIEW_SESSION_SECRET", raising=False)
    app = create_app()
    state = _hosted_state(app)
    with TestClient(app) as client:
        denied = client.post(
            "/rpc/query",
            headers={"x-thoth-review-session": "client-chosen"},
            json={
                "jsonrpc": "2.0",
                "id": "denied",
                "method": "project/list",
                "params": {"_meta": {"idempotencyKey": "denied"}, "input": {}},
            },
        )
        assert denied.status_code == 403
        error = _record(_record(denied.json())["error"])
        assert _record(error["data"])["reason_code"] == "HOSTED_REVIEW_SESSION_REQUIRED"
        assert state.bus is None


def test_internal_abort_cannot_create_shared_bus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", DeploymentMode.HOSTED_REVIEW.value)
    monkeypatch.setenv("THOTH_WORKSPACE", str(tmp_path / "origin"))
    monkeypatch.setenv("THOTH_REVIEW_SESSION_SECRET", "internal-secret")
    monkeypatch.delenv("THOTH_REVIEW_SESSION_ID", raising=False)
    app = create_app()
    state = _hosted_state(app)
    with TestClient(app) as client:
        without_session = client.post(
            "/internal/dispatch-abort",
            headers={"x-thoth-review-internal": "internal-secret"},
            json={"operation_id": "missing"},
        )
        assert without_session.status_code == 400
        assert state.bus is None
        with_session = client.post(
            "/internal/dispatch-abort",
            headers={
                "x-thoth-review-internal": "internal-secret",
                "x-thoth-review-session": "session-a",
            },
            json={"operation_id": "missing"},
        )
        assert with_session.status_code == 200
        assert state.bus is None
        assert set(state.session_runtimes) == {"session-a"}
        _http_rpc(
            client,
            session_id="session-a",
            method="project/create",
            request_id="a-after-abort",
            input_value={
                "project_id": "project:after-abort",
                "name": "A project",
                "cutoff_at": "2026-09-24T00:00:00Z",
            },
        )
        state.bus = state.session_runtimes["session-a"].bus
        try:
            projects_b = _http_rpc(
                client,
                session_id="session-b",
                method="project/list",
                request_id="b-after-abort",
                input_value={"project_id": "system:projects"},
            )
        finally:
            state.bus = None
        assert projects_b["projects"] == []


def test_origin_session_runtime_cap_and_idle_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", DeploymentMode.HOSTED_REVIEW.value)
    monkeypatch.setenv("THOTH_WORKSPACE", str(tmp_path / "origin"))
    monkeypatch.setenv("THOTH_REVIEW_SESSION_SECRET", "internal-secret")
    monkeypatch.setenv("HOSTED_REVIEW_MAX_CONCURRENT_SESSIONS", "1")
    monkeypatch.setenv("HOSTED_REVIEW_SESSION_TTL_SECONDS", "60")
    monkeypatch.delenv("THOTH_REVIEW_SESSION_ID", raising=False)
    app = create_app()
    state = _hosted_state(app)
    with TestClient(app) as client:
        _http_rpc(
            client,
            session_id="session-a",
            method="project/list",
            request_id="a-projects",
            input_value={"project_id": "system:projects"},
        )
        denied = client.post(
            "/rpc/query",
            headers={
                "x-thoth-review-internal": "internal-secret",
                "x-thoth-review-session": "session-b",
            },
            json={
                "jsonrpc": "2.0",
                "id": "b-denied",
                "method": "project/list",
                "params": {
                    "_meta": {"idempotencyKey": "b-denied"},
                    "input": {"project_id": "system:projects"},
                },
            },
        )
        assert denied.status_code == 429
        assert _text(_record(denied.json())["detail"]) == "HOSTED_REVIEW_SESSION_LIMIT"
        state.session_last_seen["session-a"] -= 61
        _http_rpc(
            client,
            session_id="session-b",
            method="project/list",
            request_id="b-projects",
            input_value={"project_id": "system:projects"},
        )
        assert set(state.session_runtimes) == {"session-b"}
