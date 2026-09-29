"""Slow synthetic auth cannot freeze unrelated local HTTP/read operations."""

import asyncio
import json
import threading
from pathlib import Path

import httpx
import pytest
from tests.integration.storage_coverage_helpers import request, value
from tests.unit.models.test_codex_isolated_broker import make_synthetic_broker

from thoth.adapters.http.app import create_app
from thoth.adapters.models import codex_broker
from thoth.adapters.models.local_credentials import LocalModelCredentials
from thoth.adapters.storage.transaction import _AMBIENT  # pyright: ignore[reportPrivateUsage]
from thoth.apps.runtime import create_runtime


@pytest.mark.asyncio
async def test_slow_credential_status_keeps_health_and_saved_project_read_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = create_runtime(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def slow(_self: LocalModelCredentials) -> list[dict[str, object]]:
        entered.set()
        assert release.wait(3)
        return []

    try:
        created = await runtime.bus.dispatch(
                request(
                    "project/create",
                    "create",
                    {
                        "project_id": "project:nonblocking",
                        "name": "Saved",
                        "cutoff_at": "2026-09-01T00:00:00Z",
                    },
                )
            )
        value(created)
        assert created.result is not None
        operation_id = created.result["operation_id"]
        monkeypatch.setattr(LocalModelCredentials, "account_connections", slow)
        pending = asyncio.create_task(
            runtime.bus.dispatch(
                request(
                    "model/credential/list",
                    "credentials",
                    {"project_id": "project:nonblocking"},
                )
            )
        )
        assert await asyncio.to_thread(entered.wait, 3)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(runtime.bus)), base_url="http://test"
        ) as client:
            health = await asyncio.wait_for(client.get("/healthz"), timeout=1)
        saved = await asyncio.wait_for(
            runtime.bus.query(
                request("project/read", "saved", {"project_id": "project:nonblocking"})
            ),
            timeout=1,
        )
        assert health.status_code == 200
        assert value(saved)["name"] == "Saved"
        await asyncio.wait_for(
            runtime.bus.dispatch(
                request(
                    "operation/cancel",
                    "cancel-terminal",
                    {"project_id": "project:nonblocking", "operation_id": operation_id},
                )
            ),
            timeout=1,
        )
        release.set()
        listed = await asyncio.wait_for(pending, timeout=2)
        assert value(listed)["accounts"] == []
    finally:
        release.set()
        runtime.close()


@pytest.mark.asyncio
async def test_normal_rpc_reads_isolated_account_catalog_and_pending_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, server = make_synthetic_broker(tmp_path, monkeypatch)
    workspace = broker.profile.workspace
    monkeypatch.setitem(codex_broker._BROKERS, workspace.resolve(), broker)  # pyright: ignore[reportPrivateUsage]
    runtime = create_runtime(workspace)
    try:
        original_call = server.call

        def no_db_transaction(method: str, params: dict[str, object]) -> dict[str, object]:
            assert _AMBIENT.get() is None
            return original_call(method, params)

        monkeypatch.setattr(server, "call", no_db_transaction)
        created = value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "project",
                    {
                        "project_id": "project:codex-rpc",
                        "name": "Synthetic Codex",
                        "cutoff_at": "2026-09-01T00:00:00Z",
                    },
                )
            )
        )
        assert created["project_id"] == "project:codex-rpc"
        listed = value(
            await runtime.bus.dispatch(
                request("model/credential/list", "list", {"project_id": "project:codex-rpc"})
            )
        )
        openai = next(item for item in listed["accounts"] if item["provider"] == "openai")
        assert openai["available_model_providers"] == ["codex-oauth"]
        assert openai["connection_state"] == "EXECUTION_UNVERIFIED"
        assert openai["execution_eligible"] is True
        assert openai["execution_verified"] is False
        settings = value(
            await runtime.bus.dispatch(
                request(
                    "model/settings/read", "settings", {"project_id": "project:codex-rpc"}
                )
            )
        )
        assert any(item["model"] == "synthetic-codex" for item in settings["model_options"])
        updated = value(
            await runtime.bus.dispatch(
                request(
                    "model/settings/update",
                    "choose-model",
                    {
                        "project_id": "project:codex-rpc",
                        "expected_digest": settings["settings_digest"],
                        "selection": {
                            "provider": "codex-oauth",
                            "model": "synthetic-codex",
                            "reasoning_effort": "medium",
                        },
                    },
                )
            )
        )
        assert updated["effective_settings"]["model"] == "synthetic-codex"
        ready = value(
            await runtime.bus.dispatch(
                request("workspace/ready", "ready", {"project_id": "project:codex-rpc"})
            )
        )
        assert ready["model_connected"] is True
        pending = value(
            await runtime.bus.dispatch(
                request(
                    "model/credential/register",
                    "login",
                    {"project_id": "project:codex-rpc", "provider": "openai", "api_key": ""},
                )
            )
        )
        assert pending["started"] is True and pending["connected"] is False
        assert pending["connection_state"] == "LOGIN_PENDING"
        rendered = json.dumps({"list": listed, "login": pending}, default=str)
        assert "synthetic-refresh-do-not-log" not in rendered
        assert "header." not in rendered
        assert set(server.methods) <= {"account/read", "model/list", "account/login/start"}
    finally:
        runtime.close()
