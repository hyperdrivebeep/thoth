"""The actual CLI keeps output, safe fields, exit codes and broker-close order."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console
from typer.testing import CliRunner

import thoth.cli as cli
from thoth.ports.model import ModelExecutionHold


@dataclass
class AuthBackend:
    status: dict[str, object] = field(default_factory=lambda: {"connected": False})
    result: dict[str, object] = field(default_factory=lambda: {"connected": True})
    fail_at: str | None = None
    events: list[str] = field(default_factory=list)


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch) -> AuthBackend:
    backend = AuthBackend()

    def status(_workspace: Path) -> dict[str, object]:
        backend.events.append("status")
        if backend.fail_at == "status":
            raise ModelExecutionHold("SYNTHETIC_HOLD")
        return backend.status

    def login(_workspace: Path, *, wait_for_completion: bool) -> dict[str, object]:
        assert wait_for_completion is True
        backend.events.append("login")
        if backend.fail_at == "login":
            raise ModelExecutionHold("SYNTHETIC_HOLD")
        return backend.result

    def close(_workspace: Path) -> None:
        backend.events.append("close")

    console = Console(width=120, force_terminal=False, color_system=None)
    original_print = console.print

    def print_output(*args: Any, **kwargs: Any) -> None:
        backend.events.append("print")
        original_print(*args, **kwargs)

    monkeypatch.setattr(console, "print", print_output)
    monkeypatch.setattr(cli, "console", console)
    monkeypatch.setattr(cli, "codex_oauth_status", status)
    monkeypatch.setattr(cli, "start_codex_login", login)
    monkeypatch.setattr(cli, "close_workspace_broker", close)
    return backend


@pytest.mark.parametrize("json_output", (False, True))
@pytest.mark.parametrize(
    ("payload", "state", "exit_code"),
    [
        ({"connected": True, "connection_state": "CONNECTED"}, "CONNECTED", 0),
        ({"connected": False, "connection_state": "LOGIN_REQUIRED"}, "LOGIN_REQUIRED", 1),
        ({"connected": False, "connection_state": ""}, "UNKNOWN", 1),
        ({"connection_state": None}, "UNKNOWN", 1),
    ],
)
def test_status_output_and_missing_connected_contract(
    backend: AuthBackend,
    tmp_path: Path,
    json_output: bool,
    payload: dict[str, object],
    state: str,
    exit_code: int,
) -> None:
    backend.status = {**payload, "token": "DO_NOT_PRINT", "cookie": "DO_NOT_PRINT"}
    args = ["auth-status", "--workspace", str(tmp_path)]
    if json_output:
        args.append("--json")
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == exit_code
    if json_output:
        assert json.loads(result.stdout) == payload
        assert list(json.loads(result.stdout)) == list(payload)
    else:
        assert result.stdout == f"Codex account: {state}\n"
    assert "DO_NOT_PRINT" not in result.output
    assert backend.events == ["status", "close", "print"]
    if "connected" not in payload:
        assert isinstance(result.exception, KeyError)


@pytest.mark.parametrize(
    ("payload", "exit_code"),
    [({"connected": True}, 0), ({"connected": False}, 1), ({"connected": 1}, 1), ({}, 1)],
)
def test_connect_safe_fields_and_strict_true_exit(
    backend: AuthBackend, tmp_path: Path, payload: dict[str, object], exit_code: int
) -> None:
    backend.result = {
        **payload,
        "guidance": "Synthetic completion",
        "auth_url": "DO_NOT_PRINT",
        "token": "DO_NOT_PRINT",
    }
    result = CliRunner().invoke(cli.app, ["auth-connect", "--workspace", str(tmp_path)])
    assert result.exit_code == exit_code
    assert json.loads(result.stdout) == {**payload, "guidance": "Synthetic completion"}
    assert "DO_NOT_PRINT" not in result.output
    assert backend.events == ["status", "login", "close", "print"]


def test_already_connected_prints_then_closes_without_login(
    backend: AuthBackend, tmp_path: Path
) -> None:
    backend.status = {"connected": True}
    result = CliRunner().invoke(cli.app, ["auth-connect", "--workspace", str(tmp_path)])
    assert result.exit_code == 0
    assert result.stdout == "Codex account is already connected in this THOTH workspace.\n"
    assert backend.events == ["status", "print", "close"]


@pytest.mark.parametrize(
    ("command", "fail_at", "expected"),
    [
        ("auth-status", "status", ["status", "close"]),
        ("auth-connect", "status", ["status", "close"]),
        ("auth-connect", "login", ["status", "login", "close"]),
    ],
)
def test_hold_keeps_stderr_exit_and_finally_close(
    backend: AuthBackend, tmp_path: Path, command: str, fail_at: str, expected: list[str]
) -> None:
    backend.fail_at = fail_at
    result = CliRunner().invoke(cli.app, [command, "--workspace", str(tmp_path)])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "SYNTHETIC_HOLD\n"
    assert backend.events == expected
