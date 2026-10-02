"""Claude Code sign-in through the normal RPC path, with a fake official executable."""

from __future__ import annotations

import time
from collections.abc import Mapping
from pathlib import Path

import pytest
from tests.integration.storage_coverage_helpers import request, value
from tests.unit.models.test_claude_code_login import FakeChild

from thoth.adapters.models import claude_code, claude_code_login
from thoth.apps.runtime import create_runtime

pytestmark = pytest.mark.usefixtures("xai_http_guard")

_URL = "https://claude.ai/oauth/authorize?code=true&state=synthetic-state"


def _use_broker(
    monkeypatch: pytest.MonkeyPatch, broker: claude_code_login.ClaudeCodeLoginBroker
) -> None:
    def broker_for_workspace(_workspace: Path) -> claude_code_login.ClaudeCodeLoginBroker:
        return broker

    monkeypatch.setattr(claude_code_login, "broker_for_workspace", broker_for_workspace)


def _install_fake_binary(
    monkeypatch: pytest.MonkeyPatch, workspace: Path, children: list[FakeChild]
) -> dict[str, object]:
    """Return the status the fake official executable reports; tests flip it after login."""
    reported: dict[str, object] = {
        "provider": "claude-code",
        "connection_state": "LOGIN_REQUIRED",
        "reason_code": "CLAUDE_CODE_LOGIN_REQUIRED",
        "connected": False,
        "execution_eligible": False,
    }

    def spawn(_argv: tuple[str, ...], _env: Mapping[str, str], _cwd: Path) -> FakeChild:
        child = FakeChild()
        child.say(_URL + "\n")
        children.append(child)
        return child

    def status(_workspace: Path, **_options: object) -> dict[str, object]:
        return dict(reported)

    monkeypatch.setattr(claude_code, "claude_code_status", status)
    broker = claude_code_login.ClaudeCodeLoginBroker(
        workspace,
        spawn=spawn,
        resolve=lambda: Path("C:/synthetic/claude.exe"),
        status_probe=status,
        url_wait_seconds=1.0,
    )
    _use_broker(monkeypatch, broker)
    return reported


@pytest.mark.asyncio
async def test_signing_in_with_claude_makes_the_workspace_ready_without_a_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "a"
    children: list[FakeChild] = []
    reported = _install_fake_binary(monkeypatch, workspace, children)
    runtime = create_runtime(workspace)
    try:
        listed = value(
            await runtime.bus.query(
                request("model/credential/list", "list-1", {"project_id": "system:workspace"})
            )
        )
        row = next(item for item in listed["accounts"] if item["provider"] == "anthropic")
        assert row["login_kind"] == "claude_code_login" and row["connected"] is False
        ready = value(await runtime.bus.query(request("workspace/ready", "ready-1", {})))
        assert ready["model_connected"] is False

        started = value(
            await runtime.bus.dispatch(
                request(
                    "model/credential/register",
                    "login-1",
                    {
                        "project_id": "system:workspace",
                        "provider": "anthropic",
                        "auth_method": "claude_code_login",
                    },
                )
            )
        )
        assert started["started"] is True and started["login_state"] == "PENDING"
        assert started["authorization_url"] == _URL
        assert started["account_provider"] == "anthropic"
        assert started["auth_method"] == "claude_code_login" and started["route"] == "claude-code"
        login_id = started["login_id"]

        # The official executable finishes the browser flow and reports a subscription login.
        reported.update(
            connection_state="EXECUTION_UNVERIFIED",
            reason_code="CLAUDE_CODE_EXECUTION_UNVERIFIED",
            connected=True,
            execution_eligible=True,
        )
        children[0].finish(0)
        state = "PENDING"
        for index in range(100):
            status = value(
                await runtime.bus.query(
                    request(
                        "model/credential/login/status",
                        f"status-{index}",
                        {
                            "provider": "anthropic",
                            "auth_method": "claude_code_login",
                            "login_id": login_id,
                        },
                    )
                )
            )
            state = status["login_state"]
            if state != "PENDING":
                break
            time.sleep(0.05)
        assert state == "CONNECTED"

        after = value(
            await runtime.bus.query(
                request("model/credential/list", "list-2", {"project_id": "system:workspace"})
            )
        )
        connected = next(item for item in after["accounts"] if item["provider"] == "anthropic")
        assert connected["available_model_providers"] == ["claude-code"]
        assert connected["execution_eligible"] is True
        assert connected["execution_verified"] is False
        ready_after = value(await runtime.bus.query(request("workspace/ready", "ready-2", {})))
        assert ready_after["model_connected"] is True
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_login_without_the_official_executable_reports_the_tool_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "a"

    def unavailable(_workspace: Path, **_options: object) -> dict[str, object]:
        return {
            "connection_state": "UNAVAILABLE",
            "reason_code": "CLAUDE_CODE_NOT_INSTALLED",
            "connected": False,
            "execution_eligible": False,
        }

    monkeypatch.setattr(claude_code, "claude_code_status", unavailable)

    def missing() -> Path:
        raise claude_code.ClaudeCodeUnavailable("CLAUDE_CODE_NOT_INSTALLED")

    broker = claude_code_login.ClaudeCodeLoginBroker(workspace, resolve=missing)
    _use_broker(monkeypatch, broker)
    runtime = create_runtime(workspace)
    try:
        listed = value(
            await runtime.bus.query(
                request("model/credential/list", "list", {"project_id": "system:workspace"})
            )
        )
        row = next(item for item in listed["accounts"] if item["provider"] == "anthropic")
        assert row["connection_state"] == "CLAUDE_CODE_NOT_INSTALLED"
        assert row["login_supported"] is False
        rejected = await runtime.bus.dispatch(
            request(
                "model/credential/register",
                "login",
                {
                    "project_id": "system:workspace",
                    "provider": "anthropic",
                    "auth_method": "claude_code_login",
                },
            )
        )
        assert rejected.error is not None
        assert "CLAUDE_CODE_NOT_INSTALLED" in rejected.error.message
    finally:
        runtime.close()
