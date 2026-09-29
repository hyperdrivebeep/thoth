from pathlib import Path
from typing import cast

import pytest

from thoth.adapters.models import codex_oauth
from thoth.adapters.models.auth_registry import AuthRegistry, AuthRoute
from thoth.adapters.models.local_credentials import (
    LocalModelCredentials,
    account_connections,
    register_api_key,
)
from thoth.ports.model_credentials import ModelCredentialError

pytestmark = pytest.mark.usefixtures("xai_http_guard")


def test_account_list_has_three_companies(tmp_path: Path) -> None:
    rows = account_connections(
        {
            "connected": True,
            "execution_eligible": True,
            "connection_state": "EXECUTION_UNVERIFIED",
            "reason_code": "EXECUTION_UNVERIFIED",
        },
        tmp_path,
    )
    assert [row["provider"] for row in rows] == ["openai", "anthropic", "xai"]
    assert rows[0]["oauth"] is True
    assert rows[0]["remote_auth_verified"] is None
    assert rows[0]["available_model_providers"] == ["codex-oauth"]
    assert rows[0]["execution_eligible"] is True
    assert rows[0]["execution_verified"] is False
    assert [row["login_supported"] for row in rows] == [True, False, True]
    assert rows[2]["connected"] is False
    assert all("token" not in row for row in rows)


def test_console_login_returns_https_url() -> None:
    from thoth.adapters.models.local_credentials import start_console_login

    result = start_console_login("xai", open_browser=False)
    assert str(result["browser_url"]).startswith("https://")
    assert result["provider"] == "xai"
    assert result["kind"] == "unsupported"
    assert result["started"] is False
    assert result["reason_code"] == "MODEL_ACCOUNT_LOGIN_UNSUPPORTED"


def test_key_register_uses_company_defaults(tmp_path: Path) -> None:
    recorded = register_api_key(provider="xai", model="", api_key="xai-key", root=tmp_path)
    assert recorded["provider"] == "xai"
    assert recorded["model"] == "grok-4.6"
    assert recorded["base_url"] == "https://api.x.ai/v1"
    assert "xai-key" not in recorded.values()


def test_supported_logins_use_isolated_owner_and_no_browser_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeBroker:
        def __init__(self, provider: str) -> None:
            self.provider = provider

        def start_login(self) -> dict[str, object]:
            return {"started": True, "provider": self.provider, "kind": "oauth"}

        def login_status(self, login_id: str | None = None) -> dict[str, object]:
            return {"provider": self.provider, "login_id": login_id, "state": "PENDING"}

        def cancel_login(self, login_id: str) -> dict[str, object]:
            return {"provider": self.provider, "login_id": login_id, "state": "CANCELLED"}

    def forbidden_browser(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("backend must not open a browser")

    def claude_guidance(workspace: Path) -> dict[str, object]:
        assert workspace == tmp_path
        return {"started": False, "reason_code": "CLAUDE_CODE_USER_LOGIN_REQUIRED"}

    monkeypatch.setattr(
        "thoth.adapters.models.claude_code.claude_code_login_guidance", claude_guidance
    )
    monkeypatch.setattr("webbrowser.open", forbidden_browser)

    registry = AuthRegistry()
    registry.register(
        AuthRoute(
            "openai", "codex_isolated_browser", "codex-oauth", lambda _: FakeBroker("codex-oauth")
        )
    )
    registry.register(AuthRoute("xai", "xai_device_code", "xai-oauth", lambda _: FakeBroker("xai")))
    credentials = LocalModelCredentials(tmp_path, auth_registry=registry)
    codex = credentials.start_login("openai")
    assert codex["started"] is True and codex["route"] == "codex-oauth"
    assert codex["auth_method"] == "codex_isolated_browser"
    assert credentials.start_login("anthropic") == {
        "started": False,
        "reason_code": "CLAUDE_CODE_USER_LOGIN_REQUIRED",
    }
    xai = credentials.start_login("xai")
    assert xai["started"] is True and xai["route"] == "xai-oauth"
    assert credentials.login_status("xai", "attempt")["login_id"] == "attempt"
    assert credentials.cancel_login("xai", "attempt")["state"] == "CANCELLED"
    with pytest.raises(ModelCredentialError, match="MODEL_ACCOUNT_LOGIN_UNSUPPORTED"):
        credentials.start_login("unknown-vendor")


def test_codex_status_failure_does_not_hide_workspace_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_api_key(provider="xai", model="grok-4.6", api_key="synthetic-key", root=tmp_path)

    def unavailable(_workspace: Path) -> dict[str, object]:
        raise OSError("synthetic CLI status failure")

    monkeypatch.setattr(codex_oauth, "codex_oauth_status", unavailable)
    rows = LocalModelCredentials(tmp_path).account_connections()
    assert next(row for row in rows if row["provider"] == "openai")["connected"] is False
    xai = next(row for row in rows if row["provider"] == "xai")
    assert xai["has_key"] is True
    assert xai["connected"] is True


def test_openai_api_key_stays_available_when_codex_profile_is_absent(tmp_path: Path) -> None:
    register_api_key(provider="openai", model="gpt-4.1", api_key="synthetic-key", root=tmp_path)
    row = next(
        item
        for item in account_connections(
            {"connected": False, "execution_eligible": False, "reason_code": "LOGIN_REQUIRED"},
            tmp_path,
        )
        if item["provider"] == "openai"
    )
    assert row["connected"] is True and row["has_key"] is True
    assert row["oauth"] is False
    assert row["available_model_providers"] == ["openai"]
    assert row["profile_mode"] == "THOTH_LOCAL_KEY"


def test_key_and_oauth_methods_remain_independent_in_account_row(tmp_path: Path) -> None:
    register_api_key(provider="openai", model="gpt-4.1", api_key="synthetic-key", root=tmp_path)
    row = next(
        item for item in account_connections(
            {"connected": True, "execution_eligible": True,
             "connection_state": "EXECUTION_UNVERIFIED", "reason_code": "EXECUTION_UNVERIFIED"},
            tmp_path,
        ) if item["provider"] == "openai"
    )
    methods = row["auth_methods"]
    assert isinstance(methods, list)
    methods = cast(list[dict[str, object]], methods)
    assert [method["auth_method"] for method in methods] == [
        "api_key", "codex_isolated_browser"
    ]
    assert [method["route"] for method in methods] == ["openai", "codex-oauth"]
    assert [method["connected"] for method in methods] == [True, True]
    assert methods[0]["capabilities"] == {
        "start": False, "status": True, "cancel": False, "manual_complete": False
    }
    assert methods[1]["capabilities"] == {
        "start": True, "status": True, "cancel": True, "manual_complete": False
    }


def test_missing_isolated_codex_profile_reports_typed_hold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from thoth.adapters.models.local_credentials import LocalCredentialHold

    class Unavailable:
        def start_login(self) -> dict[str, object]:
            raise LocalCredentialHold("CODEX_STANDALONE_NOT_INSTALLED")

        def login_status(self, login_id: str | None = None) -> dict[str, object]:
            raise LocalCredentialHold("CODEX_STANDALONE_NOT_INSTALLED")

        def cancel_login(self, login_id: str) -> dict[str, object]:
            raise LocalCredentialHold("CODEX_STANDALONE_NOT_INSTALLED")

    registry = AuthRegistry()
    registry.register(
        AuthRoute("openai", "codex_isolated_browser", "codex-oauth", lambda _: Unavailable())
    )
    with pytest.raises(ModelCredentialError, match="CODEX_STANDALONE_NOT_INSTALLED"):
        LocalModelCredentials(tmp_path, auth_registry=registry).start_login("openai")


def test_legacy_global_device_login_never_starts_a_cli() -> None:
    with pytest.raises(codex_oauth.CodexOAuthUnavailable, match="ISOLATED_APP_SERVER"):
        codex_oauth.run_codex_device_login()
