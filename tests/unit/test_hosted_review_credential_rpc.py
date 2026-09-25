from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from tests.integration.storage_coverage_helpers import request

from thoth.apps.runtime import create_runtime
from thoth.domain.deployment_mode import DeploymentMode
from thoth.protocol.jsonrpc import RpcErrorCode


@pytest.mark.asyncio
async def test_credential_rpc_is_denied_before_secrets_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", DeploymentMode.HOSTED_REVIEW.value)
    monkeypatch.setenv("THOTH_WORKSPACE", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    runtime = create_runtime(tmp_path, deployment_mode=DeploymentMode.HOSTED_REVIEW)
    try:
        listed = await runtime.bus.dispatch(
            request("model/credential/list", "cred-list", {"project_id": "system:workspace"})
        )
        assert listed.error is not None
        assert listed.error.code == RpcErrorCode.AUTHORIZATION_DENIED
        assert listed.error.data["reason_code"] == "HOSTED_REVIEW_CREDENTIAL_RPC_DENIED"
        assert listed.error.data["pre_io"] is True
        registered = await runtime.bus.dispatch(
            request(
                "model/credential/register",
                "cred-reg",
                {
                    "project_id": "system:workspace",
                    "provider": "openai",
                    "api_key": "should-not-be-stored",
                },
            )
        )
        assert registered.error is not None
        assert registered.error.data["reason_code"] == "HOSTED_REVIEW_CREDENTIAL_RPC_DENIED"
        secrets = tmp_path / "model-registry" / "secrets.json"
        assert not secrets.exists()
        locked = await runtime.bus.dispatch(
            request(
                "model/settings/update",
                "route",
                {
                    "project_id": "p",
                    "selection": {"provider": "xai", "model": "grok-4.6"},
                },
            )
        )
        assert locked.error is not None
        assert locked.error.message == "MODEL_ROUTE_LOCKED"
    finally:
        runtime.close()

    from fastapi.testclient import TestClient

    from thoth.adapters.http.app import create_app

    monkeypatch.setenv("THOTH_REVIEW_SESSION_ID", "review-session")
    app = create_app()
    tasks: object = app.state.background_tasks
    assert isinstance(tasks, dict)
    assert all(isinstance(key, str) for key in cast(dict[object, object], tasks))
    background_tasks = cast(dict[str, object], tasks)
    client = TestClient(app)
    listed = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": "http-list",
            "method": "model/credential/list",
            "params": {
                "_meta": {"idempotencyKey": "http-list"},
                "input": {"project_id": "system:workspace"},
            },
        },
    )
    assert listed.status_code == 403
    assert listed.json()["error"]["data"]["reason_code"] == "HOSTED_REVIEW_CREDENTIAL_RPC_DENIED"
    denied = client.post(
        "/rpc/query",
        json={
            "jsonrpc": "2.0",
            "id": "http-projects-denied",
            "method": "project/list",
            "params": {"_meta": {"idempotencyKey": "http-projects-denied"}, "input": {}},
        },
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["data"]["reason_code"] == "HOSTED_REVIEW_SESSION_REQUIRED"
    assert client.post("/auth/session", json={}).status_code == 404
    assert client.get("/internal/workspace-export").status_code == 404
    assert client.post("/internal/dispatch-release", json={"operation_id": "op"}).status_code == 404
    exported = client.get(
        "/internal/workspace-export",
        headers={"x-thoth-review-session": "review-session"},
    )
    assert exported.status_code == 200
    assert exported.content[:2] == b"\x1f\x8b"
    monkeypatch.setenv("THOTH_REVIEW_SESSION_SECRET", "internal-secret")
    by_secret = client.get(
        "/internal/workspace-export",
        headers={
            "x-thoth-review-internal": "internal-secret",
            "x-thoth-review-session": "cookie-session-id",
        },
    )
    assert by_secret.status_code == 200
    restored = client.post(
        "/internal/workspace-restore",
        headers={
            "x-thoth-review-internal": "internal-secret",
            "x-thoth-review-session": "cookie-session-id",
        },
        content=exported.content,
    )
    assert restored.status_code == 200
    assert restored.json().get("skipped") is not True
    assert "restored_members" in restored.json()
    background_tasks["live"] = object()
    skipped = client.post(
        "/internal/workspace-restore",
        headers={
            "x-thoth-review-internal": "internal-secret",
            "x-thoth-review-session": "cookie-session-id",
        },
        content=exported.content,
    )
    assert skipped.status_code == 200
    assert skipped.json() == {"skipped": True, "reason": "live-runtime"}
    background_tasks.clear()
    skipped_disk = client.post(
        "/internal/workspace-restore",
        headers={
            "x-thoth-review-internal": "internal-secret",
            "x-thoth-review-session": "cookie-session-id",
        },
        content=exported.content,
    )
    assert skipped_disk.status_code == 200
    assert skipped_disk.json() == {"skipped": True, "reason": "live-runtime"}
    allowed = client.post(
        "/rpc/query",
        headers={"x-thoth-review-session": "review-session"},
        json={
            "jsonrpc": "2.0",
            "id": "http-projects-allowed",
            "method": "project/list",
            "params": {"_meta": {"idempotencyKey": "http-projects-allowed"}, "input": {}},
        },
    )
    assert allowed.status_code == 200
    assert allowed.json().get("error", {}).get("data", {}).get("reason_code") != (
        "HOSTED_REVIEW_SESSION_REQUIRED"
    )
