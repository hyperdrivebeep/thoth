"""Claude Code sign-in driven through the official executable, without a terminal.

The executable owns the browser flow, the token exchange and its credential store, all inside
THOTH's isolated `CLAUDE_CONFIG_DIR`. THOTH only starts it, reads a claude.ai or claude.com link
from its output for the screen, optionally forwards a pasted code to its stdin, and confirms the
result through the executable's own status. It never reads or copies credential files.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Protocol, cast
from urllib.parse import urlsplit

from thoth.adapters.models.claude_code import (
    ClaudeCodeUnavailable,
    _child_environment,  # pyright: ignore[reportPrivateUsage]
    claude_code_status,
    claude_profile_dir,
    invalidate_claude_code_status,
    resolve_claude_executable,
)

_LOGIN_LIFETIME_SECONDS = 600.0
_URL_WAIT_SECONDS = 8.0
_KILL_CONFIRM_SECONDS = 3.0
_MAX_RESPONSE_CHARS = 8192
_MAX_BUFFER_CHARS = 16384
_URL_HOSTS = ("claude.ai", "claude.com")
_URL = re.compile(r"https://[^\s\"'<>`]+")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_PROMPT = re.compile(r"paste|authorization code|enter (?:the )?code", re.IGNORECASE)
_TERMINAL = frozenset({"CONNECTED", "DENIED", "EXPIRED", "CANCELLED", "FAILED"})


class LoginChild(Protocol):
    """The running `claude auth login` process tree, seen through blocking calls."""

    @property
    def stdin(self) -> BinaryIO: ...
    @property
    def stdout(self) -> BinaryIO: ...
    @property
    def stderr(self) -> BinaryIO: ...

    def poll(self) -> int | None: ...
    def kill_tree(self) -> bool: ...


LoginSpawner = Callable[[tuple[str, ...], Mapping[str, str], Path], LoginChild]


class _JobLoginChild:
    def __init__(self, job: object) -> None:
        from thoth.adapters.models.windows_process_job import WindowsJobProcess

        assert isinstance(job, WindowsJobProcess)
        self._job = job

    @property
    def stdin(self) -> BinaryIO:
        return self._job.stdin

    @property
    def stdout(self) -> BinaryIO:
        return self._job.stdout

    @property
    def stderr(self) -> BinaryIO:
        return self._job.stderr

    def poll(self) -> int | None:
        return self._job.poll()

    def kill_tree(self) -> bool:
        with suppress(OSError):
            self._job.kill()
        return _confirm_exit(self.poll)


class _PosixLoginChild:
    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        self._process = process

    @property
    def stdin(self) -> BinaryIO:
        assert self._process.stdin is not None
        return cast(BinaryIO, self._process.stdin)

    @property
    def stdout(self) -> BinaryIO:
        assert self._process.stdout is not None
        return cast(BinaryIO, self._process.stdout)

    @property
    def stderr(self) -> BinaryIO:
        assert self._process.stderr is not None
        return cast(BinaryIO, self._process.stderr)

    def poll(self) -> int | None:
        return self._process.poll()

    def kill_tree(self) -> bool:
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(self._process.pid, signal.SIGKILL)
        return _confirm_exit(self.poll)


def _confirm_exit(poll: Callable[[], int | None]) -> bool:
    deadline = time.monotonic() + _KILL_CONFIRM_SECONDS
    while poll() is None:
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


def spawn_login_process(argv: tuple[str, ...], env: Mapping[str, str], cwd: Path) -> LoginChild:
    if sys.platform == "win32":
        from thoth.adapters.models.windows_process_job import spawn_windows_job

        return _JobLoginChild(spawn_windows_job(argv, env, cwd))
    return _PosixLoginChild(
        subprocess.Popen(
            argv,
            env=dict(env),
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    )


def _claude_login_url(candidate: str) -> str | None:
    """Accept only a plain https link on claude.ai or claude.com."""
    try:
        parts = urlsplit(candidate.rstrip(").,;:"))
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        return None
    if parts.scheme != "https" or parts.username or parts.password or port not in (None, 443):
        return None
    if not any(host == name or host.endswith(f".{name}") for name in _URL_HOSTS):
        return None
    return parts.geturl()


@dataclass
class _Attempt:
    login_id: str
    child: LoginChild
    expires_at: float
    state: str = "PENDING"
    reason_code: str | None = None
    url: str | None = None
    prompt_seen: bool = False
    cleanup_confirmed: bool | None = None
    url_ready: threading.Event = field(default_factory=threading.Event)
    buffers: dict[str, str] = field(default_factory=lambda: {"stdout": "", "stderr": ""})


class ClaudeCodeLoginBroker:
    """One sign-in attempt at a time for one THOTH workspace profile."""

    def __init__(
        self,
        workspace: Path,
        *,
        spawn: LoginSpawner = spawn_login_process,
        resolve: Callable[[], Path] = resolve_claude_executable,
        status_probe: Callable[[Path], dict[str, object]] = claude_code_status,
        clock: Callable[[], float] = time.time,
        lifetime_seconds: float = _LOGIN_LIFETIME_SECONDS,
        url_wait_seconds: float = _URL_WAIT_SECONDS,
        invalidate: Callable[[Path | None], None] = invalidate_claude_code_status,
    ) -> None:
        self._workspace = workspace.resolve()
        self._spawn, self._resolve, self._status_probe = spawn, resolve, status_probe
        self._clock, self._lifetime, self._url_wait = clock, lifetime_seconds, url_wait_seconds
        self._invalidate = invalidate
        self._lock = threading.RLock()
        self._attempt: _Attempt | None = None

    # -- AuthHandler ----------------------------------------------------------------------

    def start_login(self) -> dict[str, object]:
        executable = self._resolve()
        with self._lock:
            previous = self._attempt
            if previous is not None and previous.state == "PENDING":
                self._finish(previous, "CANCELLED", "CLAUDE_CODE_LOGIN_SUPERSEDED", stop=True)
            self._attempt = None
            profile = claude_profile_dir(self._workspace)
            profile.mkdir(parents=True, exist_ok=True)
            child = self._spawn(
                (str(executable), "auth", "login", "--claudeai"),
                _child_environment(self._workspace),
                profile,
            )
            attempt = _Attempt(uuid.uuid4().hex, child, self._clock() + self._lifetime)
            self._attempt = attempt
        for name, stream in (("stdout", child.stdout), ("stderr", child.stderr)):
            threading.Thread(target=self._read, args=(attempt, name, stream), daemon=True).start()
        threading.Thread(target=self._watch, args=(attempt,), daemon=True).start()
        deadline = time.monotonic() + self._url_wait
        while (
            not attempt.url_ready.wait(0.02)
            and child.poll() is None
            and time.monotonic() < deadline
        ):
            pass
        with self._lock:
            if attempt.state == "PENDING" and child.poll() not in (None, 0):
                self._finish(attempt, "FAILED", "CLAUDE_CODE_LOGIN_START_FAILED", stop=False)
                self._attempt = None
                raise ClaudeCodeUnavailable("CLAUDE_CODE_LOGIN_START_FAILED")
            return self._public(attempt, started=True)

    def login_status(self, login_id: str | None = None) -> dict[str, object]:
        with self._lock:
            attempt = self._matching(login_id)
            self._expire_if_needed(attempt)
            return self._public(attempt)

    def cancel_login(self, login_id: str) -> dict[str, object]:
        with self._lock:
            attempt = self._matching(login_id)
            if attempt.state == "PENDING":
                self._finish(attempt, "CANCELLED", "CLAUDE_CODE_LOGIN_CANCELLED", stop=True)
            return self._public(attempt)

    def submit_login_response(self, login_id: str, response: str) -> dict[str, object]:
        with self._lock:
            attempt = self._matching(login_id)
            self._expire_if_needed(attempt)
            if attempt.state != "PENDING":
                raise ClaudeCodeUnavailable("CLAUDE_CODE_LOGIN_NOT_PENDING")
            text = response.strip()
            if not text or len(text) > _MAX_RESPONSE_CHARS or "\n" in text or "\r" in text:
                raise ClaudeCodeUnavailable("CLAUDE_CODE_LOGIN_RESPONSE_INVALID")
            try:
                attempt.child.stdin.write(text.encode() + b"\n")
                attempt.child.stdin.flush()
            except (OSError, ValueError) as exc:
                raise ClaudeCodeUnavailable("CLAUDE_CODE_LOGIN_INPUT_UNAVAILABLE") from exc
            return self._public(attempt)

    def close(self) -> None:
        with self._lock:
            attempt = self._attempt
            if attempt is not None and attempt.state == "PENDING":
                self._finish(attempt, "CANCELLED", "CLAUDE_CODE_LOGIN_CANCELLED", stop=True)

    # -- internals ------------------------------------------------------------------------

    def _matching(self, login_id: str | None) -> _Attempt:
        attempt = self._attempt
        if attempt is None or (login_id is not None and attempt.login_id != login_id):
            raise ClaudeCodeUnavailable("CLAUDE_CODE_LOGIN_NOT_FOUND")
        return attempt

    def _finish(self, attempt: _Attempt, state: str, reason: str | None, *, stop: bool) -> None:
        attempt.state, attempt.reason_code = state, reason
        if stop:
            attempt.cleanup_confirmed = attempt.child.kill_tree()
            if not attempt.cleanup_confirmed:
                attempt.reason_code = "CLAUDE_CODE_CLEANUP_UNCONFIRMED"
        self._invalidate(self._workspace)

    def _expire_if_needed(self, attempt: _Attempt) -> None:
        if attempt.state == "PENDING" and self._clock() >= attempt.expires_at:
            self._finish(attempt, "EXPIRED", "CLAUDE_CODE_LOGIN_EXPIRED", stop=True)

    def _read(self, attempt: _Attempt, name: str, stream: BinaryIO) -> None:
        read = getattr(stream, "read1", stream.read)
        try:
            while True:
                chunk = read(4096)
                if not chunk:
                    break
                self._scan(attempt, name, chunk.decode("utf-8", errors="replace"), final=False)
        except (OSError, ValueError):
            pass
        self._scan(attempt, name, "", final=True)

    def _scan(self, attempt: _Attempt, name: str, text: str, *, final: bool) -> None:
        clean = _ANSI.sub("", attempt.buffers[name] + text)[-_MAX_BUFFER_CHARS:]
        attempt.buffers[name] = clean
        if _PROMPT.search(clean):
            attempt.prompt_seen = True
        if attempt.url is None:
            for match in _URL.finditer(clean):
                if match.end() >= len(clean) and not final:
                    break  # the link may still be arriving
                url = _claude_login_url(match.group(0))
                if url is not None:
                    attempt.url = url
                    attempt.url_ready.set()
                    break

    def _watch(self, attempt: _Attempt) -> None:
        """After the process ends, confirm the result once through the executable's status."""
        while attempt.child.poll() is None:
            if attempt.state != "PENDING":
                return
            if self._clock() >= attempt.expires_at:
                # The screen may be closed, so no status call will ever apply the expiry.
                with self._lock:
                    if attempt.state == "PENDING":
                        self._finish(attempt, "EXPIRED", "CLAUDE_CODE_LOGIN_EXPIRED", stop=True)
                return
            time.sleep(0.1)
        code = attempt.child.poll()
        if attempt.state != "PENDING":
            return
        if code != 0:
            with self._lock:
                if attempt.state == "PENDING":
                    self._finish(attempt, "FAILED", "CLAUDE_CODE_LOGIN_FAILED", stop=False)
            return
        try:
            status = self._status_probe(self._workspace)
        except (OSError, subprocess.TimeoutExpired):
            status = {}
        with self._lock:
            if attempt.state != "PENDING":
                return
            if status.get("connected") is True:
                self._finish(attempt, "CONNECTED", None, stop=False)
            else:
                self._finish(attempt, "FAILED", "CLAUDE_CODE_LOGIN_NOT_CONFIRMED", stop=False)

    def _public(self, attempt: _Attempt, *, started: bool = False) -> dict[str, object]:
        connected = attempt.state == "CONNECTED"
        result: dict[str, object] = {
            "provider": "claude-code",
            "kind": "claude_code_login",
            "login_id": attempt.login_id,
            "state": attempt.state,
            "login_state": attempt.state,
            "auth_state": "CONNECTED" if connected else "DISCONNECTED",
            "catalog_state": "AVAILABLE" if connected else "UNAVAILABLE",
            "connected": connected,
            "profile_mode": "THOTH_ISOLATED",
            "expires_at": int(attempt.expires_at),
            "manual_response_required": attempt.state == "PENDING" and attempt.prompt_seen,
            "capabilities": {
                "start": True,
                "status": True,
                "cancel": True,
                "manual_complete": True,
            },
        }
        if started:
            result["started"] = True
        if attempt.reason_code is not None:
            result["reason_code"] = attempt.reason_code
        if attempt.cleanup_confirmed is not None:
            result["cleanup_confirmed"] = attempt.cleanup_confirmed
        if attempt.state == "PENDING" and attempt.url is not None:
            result["authorization_url"] = attempt.url
        return result


_BROKERS: dict[Path, ClaudeCodeLoginBroker] = {}
_BROKER_USERS: dict[Path, int] = {}
_BROKERS_LOCK = threading.RLock()


def broker_for_workspace(workspace: Path) -> ClaudeCodeLoginBroker:
    key = workspace.resolve()
    with _BROKERS_LOCK:
        broker = _BROKERS.get(key)
        if broker is None:
            broker = ClaudeCodeLoginBroker(key)
            _BROKERS[key] = broker
        return broker


def retain_workspace_broker(workspace: Path) -> ClaudeCodeLoginBroker:
    key = workspace.resolve()
    with _BROKERS_LOCK:
        broker = broker_for_workspace(key)
        _BROKER_USERS[key] = _BROKER_USERS.get(key, 0) + 1
        return broker


def release_workspace_broker(workspace: Path) -> None:
    key = workspace.resolve()
    with _BROKERS_LOCK:
        users = _BROKER_USERS.get(key, 0)
        if users <= 0:
            raise ClaudeCodeUnavailable("CLAUDE_CODE_BROKER_OWNER_MISSING")
        if users > 1:
            _BROKER_USERS[key] = users - 1
            return
        _BROKER_USERS.pop(key, None)
        broker = _BROKERS.pop(key, None)
    if broker is not None:
        broker.close()
