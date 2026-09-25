from pathlib import Path

import pytest

from thoth.adapters.models import codex_oauth
from thoth.adapters.models.codex_oauth import resolve_codex_executable
from thoth.adapters.models.local_credentials import (
    ThothLocalCatalog,
    list_credentials,
    register_api_key,
    register_thoth_local_providers,
)
from thoth.adapters.models.openai_responses import OpenAIResponsesModel
from thoth.adapters.models.registry import RegisteredModelResolver
from thoth.adapters.models.scripted import ScriptedModel
from thoth.ports.model import ModelResolutionError


def test_register_does_not_echo_secret(tmp_path: Path) -> None:
    recorded = register_api_key(
        provider="openai",
        model="gpt-4.1",
        api_key="sk-secret",
        base_url="https://api.openai.com/v1",
        root=tmp_path,
    )
    assert recorded["provider"] == "openai"
    assert "sk-secret" not in recorded.values()
    listed = list_credentials(tmp_path)
    assert listed[0]["model"] == "gpt-4.1"
    assert "sk-secret" not in listed[0].values()
    options = ThothLocalCatalog(tmp_path).options()
    assert options[0].provider == "openai"
    secrets = (tmp_path / "model-registry" / "secrets.json").read_text(encoding="utf-8")
    assert "sk-secret" in secrets


def test_local_provider_registers(tmp_path: Path) -> None:
    register_api_key(
        provider="openai",
        model="gpt-4.1",
        api_key="sk-secret",
        base_url="https://api.openai.com/v1",
        root=tmp_path,
    )
    resolver = RegisteredModelResolver()
    register_thoth_local_providers(resolver, tmp_path)
    assert resolver.contains("openai")


def test_malformed_index_lists_only_string_model_ids(tmp_path: Path) -> None:
    directory = tmp_path / "model-registry"
    directory.mkdir()
    (directory / "index.json").write_text(
        '{"openai":{"kind":"api_key","base_url":"https://api.openai.com/v1",'
        '"models":[null,7,"gpt-4.1"]}}',
        encoding="utf-8",
    )
    listed = list_credentials(tmp_path)
    assert [item["model"] for item in listed] == ["gpt-4.1"]


def test_public_codex_executable_lookup_keeps_existing_resolver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = tmp_path / "codex.exe"
    monkeypatch.setattr(codex_oauth, "_resolve_codex_executable", lambda: expected)
    assert resolve_codex_executable() == expected


def test_dynamic_credential_factory_sees_key_added_after_registration(tmp_path: Path) -> None:
    from thoth.adapters.models.local_credentials import register_thoth_local_providers

    workspace_a = tmp_path / "a"
    workspace_b = tmp_path / "b"
    resolver_a = RegisteredModelResolver()
    resolver_b = RegisteredModelResolver()
    register_thoth_local_providers(resolver_a, workspace_a)
    register_thoth_local_providers(resolver_b, workspace_b)
    assert resolver_a.contains("openai") is False
    register_api_key(
        provider="openai",
        model="gpt-test",
        api_key="sk-not-a-real-key",
        base_url="https://example.invalid/v1",
        root=workspace_a,
    )
    assert resolver_a.contains("openai") is True
    assert isinstance(resolver_a.resolve(provider="openai", model="gpt-test"), OpenAIResponsesModel)
    assert resolver_b.contains("openai") is False
    with pytest.raises(ModelResolutionError, match="not registered"):
        resolver_b.resolve(provider="openai", model="gpt-test")


def test_explicit_provider_factory_keeps_priority_over_workspace_fallback(tmp_path: Path) -> None:
    from thoth.adapters.models.local_credentials import register_thoth_local_providers

    register_api_key(
        provider="openai",
        model="gpt-test",
        api_key="sk-not-a-real-key",
        base_url="https://example.invalid/v1",
        root=tmp_path,
    )
    injected = ScriptedModel({})
    resolver = RegisteredModelResolver()
    resolver.register("openai", lambda _model: injected)
    register_thoth_local_providers(resolver, tmp_path)
    assert resolver.resolve(provider="openai", model="gpt-test") is injected
