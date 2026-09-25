from pathlib import Path

import pytest

from thoth.adapters.models import codex_oauth, local_credentials
from thoth.adapters.models.local_credentials import (
    LocalModelCredentials,
    account_connections,
    register_api_key,
)
from thoth.ports.model_credentials import ModelCredentialError


def test_account_list_has_three_companies(tmp_path: Path) -> None:
    rows = account_connections({"connected": True}, tmp_path)
    assert [row["provider"] for row in rows] == ["openai", "anthropic", "xai"]
    assert rows[0]["oauth"] is True
    assert rows[0]["remote_auth_verified"] is None
    assert rows[0]["available_model_providers"] == ["codex-oauth"]
    assert all(row["login_supported"] is (row["provider"] == "openai") for row in rows)
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


def test_only_supported_login_starts_and_console_routes_guide_key_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_codex_login() -> dict[str, object]:
        return {"started": True, "provider": "codex-oauth", "kind": "oauth"}

    def forbidden_browser(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("backend must not open a browser")

    def forbidden_claude_login() -> dict[str, object]:
        raise AssertionError("unsupported login must not launch a CLI")

    monkeypatch.setattr(local_credentials, "start_codex_login", fake_codex_login)
    monkeypatch.setattr(local_credentials, "start_claude_login", forbidden_claude_login)
    monkeypatch.setattr("webbrowser.open", forbidden_browser)
    credentials = LocalModelCredentials(tmp_path)
    assert credentials.start_login("openai") == {
        "started": True,
        "provider": "codex-oauth",
        "kind": "oauth",
    }
    for provider in ("anthropic", "xai"):
        result = credentials.start_login(provider)
        assert result["kind"] == "unsupported"
        assert result["started"] is False
        assert result["reason_code"] == "MODEL_ACCOUNT_LOGIN_UNSUPPORTED"
        assert str(result["browser_url"]).startswith("https://")
    with pytest.raises(ModelCredentialError, match="MODEL_ACCOUNT_LOGIN_UNSUPPORTED"):
        credentials.start_login("unknown-vendor")


def test_codex_status_failure_does_not_hide_workspace_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_api_key(provider="xai", model="grok-4.6", api_key="synthetic-key", root=tmp_path)

    def unavailable() -> dict[str, object]:
        raise OSError("synthetic CLI status failure")

    monkeypatch.setattr(codex_oauth, "codex_oauth_status", unavailable)
    rows = LocalModelCredentials(tmp_path).account_connections()
    assert next(row for row in rows if row["provider"] == "openai")["connected"] is False
    xai = next(row for row in rows if row["provider"] == "xai")
    assert xai["has_key"] is True
    assert xai["connected"] is True


def test_missing_codex_cli_reports_key_entry_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from thoth.adapters.models.local_credentials import LocalCredentialHold

    def unavailable() -> dict[str, object]:
        raise LocalCredentialHold("CODEX_CLI_NOT_FOUND")

    monkeypatch.setattr(local_credentials, "start_codex_login", unavailable)
    with pytest.raises(ModelCredentialError, match="CODEX_CLI_NOT_FOUND"):
        LocalModelCredentials(tmp_path).start_login("openai")


def test_posix_codex_device_login_never_waits_inside_the_http_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = tmp_path / "codex"
    cli.write_text("synthetic executable", encoding="utf-8")
    monkeypatch.setattr(local_credentials.sys, "platform", "linux")
    monkeypatch.setattr(codex_oauth, "resolve_codex_executable", lambda: cli)

    def forbidden_process(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("device login must not launch or block a server request")

    monkeypatch.setattr(local_credentials.subprocess, "run", forbidden_process)
    monkeypatch.setattr(local_credentials.subprocess, "Popen", forbidden_process)
    assert LocalModelCredentials(tmp_path).start_login("openai") == {
        "started": False,
        "provider": "codex-oauth",
        "kind": "manual_device_auth",
        "reason_code": "CODEX_DEVICE_AUTH_TERMINAL_REQUIRED",
    }


def test_windows_codex_login_still_starts_a_separate_console(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = tmp_path / "codex.exe"
    cli.write_text("synthetic executable", encoding="utf-8")
    monkeypatch.setattr(local_credentials.sys, "platform", "win32")
    monkeypatch.setattr(local_credentials.subprocess, "CREATE_NEW_CONSOLE", 16, raising=False)
    monkeypatch.setattr(codex_oauth, "resolve_codex_executable", lambda: cli)
    launched: list[tuple[list[str], int]] = []

    def record_process(arguments: list[str], *, creationflags: int) -> None:
        launched.append((arguments, creationflags))

    monkeypatch.setattr(local_credentials.subprocess, "Popen", record_process)
    assert LocalModelCredentials(tmp_path).start_login("openai") == {
        "started": True,
        "provider": "codex-oauth",
        "kind": "oauth",
    }
    assert launched == [([str(cli), "login", "--device-auth"], 16)]
