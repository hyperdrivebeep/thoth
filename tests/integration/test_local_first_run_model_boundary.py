"""Fresh LOCAL model setup uses only THOTH workspace credentials or supported login."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.integration.storage_coverage_helpers import request, value

from thoth.adapters.models import codex_oauth
from thoth.adapters.models.catalog import StaticModelCatalog
from thoth.apps.runtime import AppRuntime, create_runtime
from thoth.domain.model_settings import ModelOption, ModelSelection


def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "synthetic-home"
    codex_home = home / ".codex"
    omo = home / ".omo" / "agent"
    codex_home.mkdir(parents=True)
    omo.mkdir(parents=True)
    (omo / "auth.json").write_text(
        '{"xai":{"type":"oauth","access":"never-read-omo-secret"}}', encoding="utf-8"
    )
    (omo / "models-store.json").write_text(
        '{"xai":{"models":[{"id":"grok-4.6","api":"openai-responses",'
        '"baseUrl":"https://api.x.ai/v1"}]}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    def fake_home(_cls: type[Path]) -> Path:
        return home

    def disconnected() -> dict[str, object]:
        return {"provider": "codex-oauth", "connected": False}

    monkeypatch.setattr(Path, "home", classmethod(fake_home))
    monkeypatch.setattr(codex_oauth, "codex_oauth_status", disconnected)
    return home


async def _create_project(runtime: AppRuntime, project_id: str) -> None:
    bus = runtime.bus
    value(
        await bus.dispatch(
            request(
                "project/create",
                f"create-{project_id}",
                {"project_id": project_id, "name": project_id, "cutoff_at": "2026-09-24T00:00:00Z"},
            )
        )
    )


@pytest.mark.asyncio
async def test_omo_sentinel_does_not_connect_a_fresh_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_home(tmp_path, monkeypatch)
    runtime = create_runtime(tmp_path / "a")
    try:
        ready = value(await runtime.bus.query(request("workspace/ready", "ready-a", {})))
        listed = value(
            await runtime.bus.query(
                request(
                    "model/credential/list",
                    "credentials-a",
                    {"project_id": "system:workspace"},
                )
            )
        )
        assert ready["model_connected"] is False
        assert listed["credentials"] == []
        xai = next(row for row in listed["accounts"] if row["provider"] == "xai")
        assert xai["connected"] is False
        assert xai["oauth"] is False
        assert xai["available_model_providers"] == []
        await _create_project(runtime, "project:fresh")
        settings = value(
            await runtime.bus.query(
                request("model/settings/read", "fresh-settings", {"project_id": "project:fresh"})
            )
        )
        assert settings["availability"] == "UNAVAILABLE"
        assert settings["effective_settings"] is None
        assert settings["reason_code"] == "MODEL_CAPABILITY_UNKNOWN"
        assert "never-read-omo-secret" not in str((ready, listed))
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_key_registration_updates_same_runtime_and_stays_in_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_home(tmp_path, monkeypatch)
    runtime_a = create_runtime(tmp_path / "a")
    runtime_b = create_runtime(tmp_path / "b")
    try:
        await _create_project(runtime_a, "project:a")
        await _create_project(runtime_b, "project:b")
        value(
            await runtime_a.bus.dispatch(
                request(
                    "model/credential/register",
                    "key-a",
                    {
                        "project_id": "system:workspace",
                        "provider": "openai",
                        "model": "gpt-test",
                        "api_key": "sk-workspace-a-test",
                        "base_url": "https://example.invalid/v1",
                    },
                )
            )
        )
        ready_a = value(await runtime_a.bus.query(request("workspace/ready", "ready-a", {})))
        ready_b = value(await runtime_b.bus.query(request("workspace/ready", "ready-b", {})))
        assert ready_a["model_connected"] is True
        assert ready_b["model_connected"] is False
        accounts_a = value(
            await runtime_a.bus.query(
                request("model/credential/list", "list-a", {"project_id": "system:workspace"})
            )
        )
        openai = next(row for row in accounts_a["accounts"] if row["provider"] == "openai")
        assert openai["has_key"] is True
        assert openai["oauth"] is False
        assert openai["remote_auth_verified"] is None
        assert openai["available_model_providers"] == ["openai"]
        settings_a = value(
            await runtime_a.bus.query(
                request("model/settings/read", "settings-a", {"project_id": "project:a"})
            )
        )
        settings_b = value(
            await runtime_b.bus.query(
                request("model/settings/read", "settings-b", {"project_id": "project:b"})
            )
        )
        assert settings_a["availability"] == "AVAILABLE"
        assert (
            settings_a["effective_settings"]["provider"],
            settings_a["effective_settings"]["model"],
        ) == ("openai", "gpt-test")
        assert not any(item["provider"] == "openai" for item in settings_b["model_options"])
        assert "sk-workspace-a-test" not in str((ready_a, settings_a, settings_b))
    finally:
        runtime_a.close()
        runtime_b.close()


@pytest.mark.asyncio
async def test_saved_unavailable_model_is_preserved_while_other_workspace_option_is_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_home(tmp_path, monkeypatch)
    workspace = tmp_path / "a"
    runtime = create_runtime(workspace)
    try:
        await _create_project(runtime, "project:a")
        for provider, model in (("openai", "gpt-test"), ("xai", "grok-4.6")):
            value(
                await runtime.bus.dispatch(
                    request(
                        "model/credential/register",
                        f"key-{provider}",
                        {
                            "project_id": "system:workspace",
                            "provider": provider,
                            "model": model,
                            "api_key": f"synthetic-{provider}-key",
                        },
                    )
                )
            )
        selected = value(
            await runtime.bus.dispatch(
                request(
                    "model/settings/update",
                    "select-openai",
                    {
                        "project_id": "project:a",
                        "selection": {
                            "provider": "openai",
                            "model": "gpt-test",
                            "reasoning_effort": "high",
                        },
                        "expected_digest": None,
                    },
                )
            )
        )
        secret_path = workspace / "model-registry" / "secrets.json"
        secrets = json.loads(secret_path.read_text(encoding="utf-8"))
        secrets.pop("openai")
        secret_path.write_text(json.dumps(secrets), encoding="utf-8")
        before_heads = dict(runtime.ledger.read_heads("project:a"))
        ready = value(await runtime.bus.query(request("workspace/ready", "ready", {})))
        assert ready["model_connected"] is True
        unavailable = value(
            await runtime.bus.query(
                request("model/settings/read", "unavailable", {"project_id": "project:a"})
            )
        )
        assert unavailable["availability"] == "UNAVAILABLE"
        assert unavailable["reason_code"] == "MODEL_CAPABILITY_UNKNOWN"
        assert unavailable["selection"] == selected["selection"]
        assert unavailable["effective_settings"] is None
        assert dict(runtime.ledger.read_heads("project:a")) == before_heads
        rejected = await runtime.bus.dispatch(
            request(
                "model/settings/update",
                "invalid-again",
                {
                    "project_id": "project:a",
                    "selection": selected["selection"],
                    "expected_digest": selected["settings_digest"],
                },
            )
        )
        assert rejected.error is not None
        assert dict(runtime.ledger.read_heads("project:a")) == before_heads
        changed = value(
            await runtime.bus.dispatch(
                request(
                    "model/settings/update",
                    "choose-xai",
                    {
                        "project_id": "project:a",
                        "selection": {
                            "provider": "xai",
                            "model": "grok-4.6",
                            "reasoning_effort": "high",
                        },
                        "expected_digest": selected["settings_digest"],
                    },
                )
            )
        )
        assert changed["availability"] == "AVAILABLE"
        assert changed["effective_settings"]["provider"] == "xai"
        assert dict(runtime.ledger.read_heads("project:a")) != before_heads
    finally:
        runtime.close()
    reopened = create_runtime(workspace)
    try:
        readback = value(
            await reopened.bus.query(
                request("model/settings/read", "read-after-reopen", {"project_id": "project:a"})
            )
        )
        assert readback["availability"] == "AVAILABLE"
        assert readback["selection"] == changed["selection"]
        assert readback["effective_settings"]["reasoning_effort"] == "high"
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_codex_status_needs_matching_catalog_option_for_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _isolated_home(tmp_path, monkeypatch)

    def connected() -> dict[str, object]:
        return {"provider": "codex-oauth", "connected": True}

    monkeypatch.setattr(codex_oauth, "codex_oauth_status", connected)
    runtime = create_runtime(tmp_path / "a")
    try:
        no_option = value(await runtime.bus.query(request("workspace/ready", "none", {})))
        assert no_option["model_connected"] is False
        (home / ".codex" / "config.toml").write_text(
            'model = "gpt-test"\nmodel_reasoning_effort = "high"\n', encoding="utf-8"
        )
        (home / ".codex" / "models_cache.json").write_text(
            json.dumps(
                {
                    "models": [
                        {
                            "slug": "gpt-test",
                            "visibility": "list",
                            "supported_reasoning_levels": [{"effort": "high"}],
                            "default_reasoning_level": "high",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        with_option = value(await runtime.bus.query(request("workspace/ready", "yes", {})))
        assert with_option["model_connected"] is True
        listed = value(
            await runtime.bus.query(
                request("model/credential/list", "oauth-list", {"project_id": "system:workspace"})
            )
        )
        openai = next(row for row in listed["accounts"] if row["provider"] == "openai")
        assert openai["oauth"] is True
        assert openai["remote_auth_verified"] is None
        assert openai["has_key"] is False
        assert openai["available_model_providers"] == ["codex-oauth"]
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_same_provider_different_model_does_not_make_workspace_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_home(tmp_path, monkeypatch)
    mismatched = StaticModelCatalog(
        (
            ModelOption(
                provider="openai",
                model="gpt-other",
                reasoning_efforts=("high",),
                default_effort="high",
                capability_source="controlled-mismatch",
            ),
        ),
        ModelSelection(provider="openai", model="gpt-other"),
    )
    runtime = create_runtime(tmp_path / "a", model_catalog=mismatched)
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "model/credential/register",
                    "key-for-other-model",
                    {
                        "project_id": "system:workspace",
                        "provider": "openai",
                        "model": "gpt-test",
                        "api_key": "sk-not-a-real-key",
                    },
                )
            )
        )
        ready = value(await runtime.bus.query(request("workspace/ready", "ready", {})))
        assert ready["model_connected"] is False
    finally:
        runtime.close()
