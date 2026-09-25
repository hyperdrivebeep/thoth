import json
from pathlib import Path

import pytest

from thoth.adapters.models.omo_native import (
    OmoNativeCatalog,
    OmoNativeUnavailable,
    omo_credential,
    omo_store_entries,
    register_omo_native_providers,
)
from thoth.adapters.models.registry import RegisteredModelResolver


def _write_omo(root: Path) -> None:
    (root / "models-store.json").write_text(
        json.dumps(
            {
                "xai": {
                    "models": [
                        {
                            "id": "grok-4.6",
                            "api": "openai-responses",
                            "baseUrl": "https://api.x.ai/v1",
                            "thinkingLevelMap": {"high": "high"},
                        }
                    ]
                },
                "openai": {
                    "models": [
                        {
                            "id": "gpt-4.1",
                            "api": "openai-responses",
                            "baseUrl": "https://api.openai.com/v1",
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    (root / "auth.json").write_text(
        json.dumps(
            {
                "xai": {"type": "oauth", "access": "xai-token"},
                "openai": {"type": "api_key", "key": "sk-test"},
            }
        ),
        encoding="utf-8",
    )


def test_store_lists_all_responses_providers(tmp_path: Path) -> None:
    _write_omo(tmp_path)
    entries = omo_store_entries(tmp_path)
    ids = {(e["provider"], e["id"]) for e in entries}
    assert ("xai", "grok-4.6") in ids
    assert ("openai", "gpt-4.1") in ids
    options = OmoNativeCatalog(tmp_path).options()
    assert {o.provider for o in options} == {"xai", "openai"}
    defaults = OmoNativeCatalog(tmp_path).defaults()
    assert (defaults.provider, defaults.model) == ("xai", "grok-4.6")


def test_api_key_and_oauth_credentials(tmp_path: Path) -> None:
    _write_omo(tmp_path)
    token, kind = omo_credential("openai", tmp_path)
    assert kind == "api_key"
    assert token == "sk-test"
    token, kind = omo_credential("xai", tmp_path)
    assert kind == "oauth"
    assert token == "xai-token"


def test_malformed_omo_auth_does_not_return_a_secret(tmp_path: Path) -> None:
    (tmp_path / "auth.json").write_text(
        '{"xai":{"type":"oauth","access":7}}', encoding="utf-8"
    )
    with pytest.raises(OmoNativeUnavailable, match="OMO_AUTH_SECRET_UNAVAILABLE"):
        omo_credential("xai", tmp_path)


def test_registers_store_providers_except_existing(tmp_path: Path) -> None:
    _write_omo(tmp_path)
    resolver = RegisteredModelResolver()
    resolver.register("xai", lambda model: None)  # type: ignore[arg-type]
    register_omo_native_providers(resolver, tmp_path)
    assert resolver.contains("openai")
    assert resolver.contains("xai")
