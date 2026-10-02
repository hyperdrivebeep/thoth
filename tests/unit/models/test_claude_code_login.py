"""Claude Code sign-in is driven through the official executable with a fake child."""

from __future__ import annotations

import contextlib
import io
import os
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

import pytest

from thoth.adapters.models.claude_code import ClaudeCodeUnavailable
from thoth.adapters.models.claude_code_login import ClaudeCodeLoginBroker

_CONNECTED: dict[str, object] = {
    "connected": True,
    "execution_eligible": True,
    "connection_state": "EXECUTION_UNVERIFIED",
}
_SIGNED_OUT: dict[str, object] = {
    "connected": False,
    "execution_eligible": False,
    "connection_state": "LOGIN_REQUIRED",
}


class FakeChild:
    """Stdout/stderr are real pipes so the broker's reader threads behave as with a process."""

    def __init__(self, *, kill_confirms: bool = True) -> None:
        out_read, self._out_write = os.pipe()
        err_read, self._err_write = os.pipe()
        self.stdout = os.fdopen(out_read, "rb")
        self.stderr = os.fdopen(err_read, "rb")
        self.stdin = io.BytesIO()
        self.code: int | None = None
        self.killed = 0
        self._kill_confirms = kill_confirms

    def say(self, text: str) -> None:
        os.write(self._out_write, text.encode())

    def finish(self, code: int) -> None:
        self.code = code
        for fd in (self._out_write, self._err_write):
            with contextlib.suppress(OSError):
                os.close(fd)

    def poll(self) -> int | None:
        return self.code

    def kill_tree(self) -> bool:
        self.killed += 1
        if self._kill_confirms:
            self.finish(-9)
        return self._kill_confirms


class Harness:
    def __init__(self, tmp_path: Path, **overrides: object) -> None:
        self.children: list[FakeChild] = []
        self.spawned: list[tuple[tuple[str, ...], dict[str, str], Path]] = []
        self.now = [1000.0]
        self.probes = 0
        self.invalidated = 0
        self.probe_result: dict[str, object] = _CONNECTED
        self.workspace = tmp_path / "workspace"

        def spawn(argv: tuple[str, ...], env: Mapping[str, str], cwd: Path) -> FakeChild:
            child = FakeChild(kill_confirms=bool(overrides.get("kill_confirms", True)))
            hook = cast("Callable[[FakeChild], None] | None", overrides.get("on_spawn"))
            if hook is not None:
                hook(child)
            self.children.append(child)
            self.spawned.append((argv, dict(env), cwd))
            return child

        def probe(_workspace: Path) -> dict[str, object]:
            self.probes += 1
            return self.probe_result

        def invalidate(_workspace: Path | None = None) -> None:
            self.invalidated += 1

        self.broker = ClaudeCodeLoginBroker(
            self.workspace,
            spawn=cast(Callable[..., object], spawn),  # type: ignore[arg-type]
            resolve=cast("Callable[[], Path]", overrides.get("resolve"))
            or (lambda: Path("C:/synthetic/claude.exe")),
            status_probe=probe,
            clock=lambda: self.now[0],
            url_wait_seconds=float(cast(float, overrides.get("url_wait_seconds", 0.3))),
            invalidate=invalidate,
        )

    def wait_for(self, state: str, login_id: str, seconds: float = 3.0) -> dict[str, object]:
        deadline = time.monotonic() + seconds
        while True:
            current = self.broker.login_status(login_id)
            if current["login_state"] == state or time.monotonic() > deadline:
                return current
            time.sleep(0.02)


def test_start_runs_the_official_login_in_the_isolated_profile_and_returns_only_a_claude_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-never-reach-cli")

    def announce(child: FakeChild) -> None:
        child.say("Opening https://evil.example/steal then visit\n")
        child.say("https://claude.ai/oauth/authorize?code=true&state=synthetic-state\n")

    harness = Harness(tmp_path, on_spawn=announce)
    started = harness.broker.start_login()
    argv, env, cwd = harness.spawned[0]
    assert argv[0].endswith("claude.exe")
    assert argv[1:] == ("auth", "login", "--claudeai")
    assert env["CLAUDE_CONFIG_DIR"].endswith(str(Path("model-profiles") / "claude-code"))
    assert "ANTHROPIC_API_KEY" not in env
    assert started["started"] is True and started["login_state"] == "PENDING"
    assert started["authorization_url"] == (
        "https://claude.ai/oauth/authorize?code=true&state=synthetic-state"
    )
    assert started["expires_at"] == 1600
    assert started["capabilities"] == {
        "start": True,
        "status": True,
        "cancel": True,
        "manual_complete": True,
    }
    assert cwd == harness.workspace.resolve() / "model-profiles" / "claude-code"
    harness.broker.close()


def test_start_without_a_printed_url_is_still_pending_and_offers_no_link(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    started = harness.broker.start_login()
    assert started["login_state"] == "PENDING"
    assert "authorization_url" not in started
    assert started["manual_response_required"] is False
    harness.broker.close()


def test_a_code_prompt_is_reported_so_the_screen_can_ask_for_it(tmp_path: Path) -> None:

    def prompt(child: FakeChild) -> None:
        child.say("Paste code here if prompted > ")

    harness = Harness(tmp_path, on_spawn=prompt)
    assert harness.broker.start_login()["manual_response_required"] is True
    harness.broker.close()


def test_status_stays_pending_until_the_process_ends_then_confirms_with_one_probe(
    tmp_path: Path,
) -> None:
    harness = Harness(tmp_path)
    login_id = str(harness.broker.start_login()["login_id"])
    assert harness.broker.login_status(login_id)["login_state"] == "PENDING"
    assert harness.probes == 0
    harness.children[0].finish(0)
    done = harness.wait_for("CONNECTED", login_id)
    assert done["login_state"] == "CONNECTED" and done["auth_state"] == "CONNECTED"
    assert "authorization_url" not in done
    assert harness.probes == 1 and harness.invalidated >= 1
    assert harness.broker.login_status(login_id)["login_state"] == "CONNECTED"
    assert harness.probes == 1


def test_a_clean_exit_that_did_not_sign_in_is_a_failure_not_a_connection(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    harness.probe_result = _SIGNED_OUT
    login_id = str(harness.broker.start_login()["login_id"])
    harness.children[0].finish(0)
    done = harness.wait_for("FAILED", login_id)
    assert done["login_state"] == "FAILED"
    assert done["reason_code"] == "CLAUDE_CODE_LOGIN_NOT_CONFIRMED"


def test_a_failing_exit_reports_the_login_failure_without_probing(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    login_id = str(harness.broker.start_login()["login_id"])
    harness.children[0].finish(1)
    done = harness.wait_for("FAILED", login_id)
    assert done["reason_code"] == "CLAUDE_CODE_LOGIN_FAILED"
    assert harness.probes == 0


def test_cancel_stops_only_the_matching_attempt_and_reports_confirmed_cleanup(
    tmp_path: Path,
) -> None:
    harness = Harness(tmp_path)
    login_id = str(harness.broker.start_login()["login_id"])
    with pytest.raises(ClaudeCodeUnavailable, match="CLAUDE_CODE_LOGIN_NOT_FOUND"):
        harness.broker.cancel_login("other-attempt")
    assert harness.children[0].killed == 0
    cancelled = harness.broker.cancel_login(login_id)
    assert cancelled["login_state"] == "CANCELLED" and cancelled["cleanup_confirmed"] is True
    assert harness.children[0].killed == 1
    assert harness.broker.login_status(login_id)["login_state"] == "CANCELLED"
    assert harness.broker.cancel_login(login_id)["login_state"] == "CANCELLED"
    assert harness.children[0].killed == 1


def test_cancel_reports_when_the_process_tree_did_not_stop(tmp_path: Path) -> None:
    harness = Harness(tmp_path, kill_confirms=False)
    login_id = str(harness.broker.start_login()["login_id"])
    cancelled = harness.broker.cancel_login(login_id)
    assert cancelled["cleanup_confirmed"] is False
    assert cancelled["reason_code"] == "CLAUDE_CODE_CLEANUP_UNCONFIRMED"


def test_a_new_login_replaces_the_previous_one(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    first = str(harness.broker.start_login()["login_id"])
    second = str(harness.broker.start_login()["login_id"])
    assert first != second and harness.children[0].killed == 1
    with pytest.raises(ClaudeCodeUnavailable, match="CLAUDE_CODE_LOGIN_NOT_FOUND"):
        harness.broker.login_status(first)
    assert harness.broker.login_status(second)["login_state"] == "PENDING"
    harness.broker.close()


def test_an_attempt_expires_after_ten_minutes_and_its_process_is_stopped(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    login_id = str(harness.broker.start_login()["login_id"])
    harness.now[0] += 599
    assert harness.broker.login_status(login_id)["login_state"] == "PENDING"
    harness.now[0] += 2
    expired = harness.broker.login_status(login_id)
    assert expired["login_state"] == "EXPIRED"
    assert harness.children[0].killed == 1


def test_an_expired_attempt_is_stopped_even_when_nobody_polls_its_status(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    login_id = str(harness.broker.start_login()["login_id"])
    assert harness.children[0].killed == 0
    harness.now[0] += 601  # the screen was closed: no status or submit call follows
    deadline = time.monotonic() + 3.0
    while harness.children[0].killed == 0 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert harness.children[0].killed == 1
    expired = harness.broker.login_status(login_id)
    assert expired["login_state"] == "EXPIRED"
    assert expired["reason_code"] == "CLAUDE_CODE_LOGIN_EXPIRED"
    assert harness.children[0].killed == 1


def test_a_pasted_code_goes_to_the_process_and_never_comes_back(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    login_id = str(harness.broker.start_login()["login_id"])
    result = harness.broker.submit_login_response(login_id, "  synthetic-code-123  ")
    assert harness.children[0].stdin.getvalue() == b"synthetic-code-123\n"
    assert result["login_state"] == "PENDING"
    assert "synthetic-code-123" not in repr(result)
    for bad in ("two\nlines", "x" * 9000, "  "):
        with pytest.raises(ClaudeCodeUnavailable, match="CLAUDE_CODE_LOGIN_RESPONSE_INVALID"):
            harness.broker.submit_login_response(login_id, bad)
    harness.broker.cancel_login(login_id)
    with pytest.raises(ClaudeCodeUnavailable, match="CLAUDE_CODE_LOGIN_NOT_PENDING"):
        harness.broker.submit_login_response(login_id, "late-code")
    assert harness.children[0].stdin.getvalue() == b"synthetic-code-123\n"


def test_missing_executable_fails_before_any_process_starts(tmp_path: Path) -> None:

    def missing() -> Path:
        raise ClaudeCodeUnavailable("CLAUDE_CODE_NOT_INSTALLED")

    harness = Harness(tmp_path, resolve=missing)
    with pytest.raises(ClaudeCodeUnavailable, match="CLAUDE_CODE_NOT_INSTALLED"):
        harness.broker.start_login()
    assert harness.spawned == []


def test_status_without_any_attempt_is_not_found(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    with pytest.raises(ClaudeCodeUnavailable, match="CLAUDE_CODE_LOGIN_NOT_FOUND"):
        harness.broker.login_status("anything")


def test_close_stops_a_live_login_process(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    harness.broker.start_login()
    harness.broker.close()
    assert harness.children[0].killed == 1


def test_default_auth_registry_routes_the_claude_login_to_this_broker(tmp_path: Path) -> None:
    from thoth.adapters.models import claude_code_login
    from thoth.adapters.models.auth_registry import default_auth_registry

    route = default_auth_registry().resolve("anthropic", "claude_code_login")
    assert route.model_route == "claude-code" and route.manual_complete is True
    assert route.factory(tmp_path) is claude_code_login.broker_for_workspace(tmp_path)


def test_runtime_owned_broker_is_closed_when_the_last_owner_releases_it(tmp_path: Path) -> None:
    from thoth.adapters.models import claude_code_login

    first = claude_code_login.retain_workspace_broker(tmp_path)
    assert claude_code_login.retain_workspace_broker(tmp_path) is first
    claude_code_login.release_workspace_broker(tmp_path)
    assert claude_code_login.broker_for_workspace(tmp_path) is first
    claude_code_login.release_workspace_broker(tmp_path)
    assert claude_code_login.broker_for_workspace(tmp_path) is not first
    with pytest.raises(ClaudeCodeUnavailable, match="CLAUDE_CODE_BROKER_OWNER_MISSING"):
        claude_code_login.release_workspace_broker(tmp_path)
