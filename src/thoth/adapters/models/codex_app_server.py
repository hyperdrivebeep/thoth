"""Narrow, isolated Codex App Server auth/catalog client. No thread or turn methods."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from collections.abc import Callable
from typing import Protocol, TextIO, cast

from thoth.adapters.models.codex_profile import (
    CodexExecutableIdentity,
    CodexProfile,
    CodexProfileHold,
)

_ALLOWED = frozenset(
    {"account/read", "account/login/start", "account/login/cancel", "model/list"}
)


class AppServerProcessPort(Protocol):
    stdin: TextIO | None
    stdout: TextIO | None

    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...
    def wait(self, timeout: float | None = None) -> int: ...


ProcessFactory = Callable[[CodexExecutableIdentity, CodexProfile], AppServerProcessPort]


def _spawn(identity: CodexExecutableIdentity, profile: CodexProfile) -> AppServerProcessPort:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(("OPENAI_", "CODEX_", "THOTH_"))
    }
    isolated = str(profile.root)
    env.update(
        CODEX_HOME=isolated,
        HOME=isolated,
        USERPROFILE=isolated,
        APPDATA=isolated,
        LOCALAPPDATA=isolated,
        XDG_CONFIG_HOME=isolated,
    )
    return cast(AppServerProcessPort, subprocess.Popen(
        [str(identity.path), "app-server", "--listen", "stdio://"],
        cwd=profile.root,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ))


class CodexAppServerClient:
    def __init__(
        self,
        profile: CodexProfile,
        identity: CodexExecutableIdentity,
        *,
        process_factory: ProcessFactory | None = None,
        timeout_seconds: float = 10,
    ) -> None:
        self.profile = profile
        self.identity = identity
        self._factory = process_factory or _spawn
        self._timeout = timeout_seconds
        self._process: AppServerProcessPort | None = None
        self._messages: queue.Queue[dict[str, object] | None] = queue.Queue()
        self._login_notifications: queue.Queue[dict[str, object]] = queue.Queue()
        self._lock = threading.RLock()
        self._next_id = 1

    @staticmethod
    def _parse(value: str) -> dict[str, object]:
        try:
            raw: object = json.loads(value)
        except ValueError as exc:
            raise CodexProfileHold("CODEX_APP_SERVER_PROTOCOL_INVALID") from exc
        if not isinstance(raw, dict):
            raise CodexProfileHold("CODEX_APP_SERVER_PROTOCOL_INVALID")
        mapping = cast(dict[object, object], raw)
        if any(not isinstance(key, str) for key in mapping):
            raise CodexProfileHold("CODEX_APP_SERVER_PROTOCOL_INVALID")
        return cast(dict[str, object], mapping)

    def _read_output(self, stream: TextIO) -> None:
        try:
            for line in stream:
                self._messages.put(self._parse(line))
        except (OSError, CodexProfileHold):
            pass
        finally:
            self._messages.put(None)

    def _send(self, payload: dict[str, object]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.poll() is not None:
            raise CodexProfileHold("CODEX_APP_SERVER_UNAVAILABLE")
        try:
            process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
            process.stdin.flush()
        except OSError as exc:
            raise CodexProfileHold("CODEX_APP_SERVER_UNAVAILABLE") from exc

    def _await(self, identifier: int) -> dict[str, object]:
        deadline = time.monotonic() + self._timeout
        while True:
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise queue.Empty
                message = self._messages.get(timeout=remaining)
            except queue.Empty as exc:
                self.close()
                raise CodexProfileHold("CODEX_APP_SERVER_TIMEOUT") from exc
            if message is None:
                self.close()
                raise CodexProfileHold("CODEX_APP_SERVER_UNAVAILABLE")
            if "id" not in message:
                if message.get("method") == "account/login/completed":
                    self._login_notifications.put(message)
                continue
            if message["id"] != identifier:
                self.close()
                raise CodexProfileHold("CODEX_APP_SERVER_PROTOCOL_INVALID")
            if "error" in message:
                raise CodexProfileHold("CODEX_APP_SERVER_REQUEST_FAILED")
            result = message.get("result")
            if not isinstance(result, dict):
                raise CodexProfileHold("CODEX_APP_SERVER_PROTOCOL_INVALID")
            return self._parse(json.dumps(result))

    def _start(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        self.profile.check_pin(self.identity)
        try:
            process = self._factory(self.identity, self.profile)
        except OSError as exc:
            raise CodexProfileHold("CODEX_APP_SERVER_UNAVAILABLE") from exc
        if process.stdin is None or process.stdout is None:
            raise CodexProfileHold("CODEX_APP_SERVER_UNAVAILABLE")
        self._process = process
        self._messages = queue.Queue()
        self._login_notifications = queue.Queue()
        threading.Thread(
            target=self._read_output, args=(process.stdout,), daemon=True, name="thoth-codex-auth"
        ).start()
        self._send(
            {
                "method": "initialize",
                "id": 0,
                "params": {
                    "clientInfo": {
                        "name": "thoth_isolated_auth",
                        "title": "THOTH Isolated Auth",
                        "version": "1.0.0",
                    },
                    "capabilities": {"experimentalApi": True},
                },
            }
        )
        self._await(0)
        self._send({"method": "initialized", "params": {}})

    def call(self, method: str, params: dict[str, object]) -> dict[str, object]:
        if method not in _ALLOWED:
            raise CodexProfileHold("CODEX_APP_SERVER_METHOD_FORBIDDEN")
        with self._lock:
            self._start()
            identifier = self._next_id
            self._next_id += 1
            self._send({"method": method, "id": identifier, "params": params})
            return self._await(identifier)

    def wait_for_login(self, login_id: str, *, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + timeout_seconds
        with self._lock:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CodexProfileHold("CODEX_LOGIN_TIMEOUT")
                try:
                    message = self._login_notifications.get_nowait()
                except queue.Empty:
                    try:
                        message = self._messages.get(timeout=remaining)
                    except queue.Empty as exc:
                        raise CodexProfileHold("CODEX_LOGIN_TIMEOUT") from exc
                if message is None:
                    raise CodexProfileHold("CODEX_APP_SERVER_UNAVAILABLE")
                if message.get("method") != "account/login/completed":
                    if "id" in message:
                        raise CodexProfileHold("CODEX_APP_SERVER_PROTOCOL_INVALID")
                    continue
                params = message.get("params")
                if not isinstance(params, dict):
                    raise CodexProfileHold("CODEX_APP_SERVER_PROTOCOL_INVALID")
                typed = cast(dict[str, object], params)
                if typed.get("loginId") != login_id:
                    continue
                return typed.get("success") is True

    def close(self) -> None:
        with self._lock:
            process, self._process = self._process, None
            if process is None or process.poll() is not None:
                return
            try:
                process.terminate()
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                    process.wait(timeout=2)
                except (OSError, subprocess.TimeoutExpired) as exc:
                    raise CodexProfileHold("CODEX_APP_SERVER_CLEANUP_FAILED") from exc
            except OSError as exc:
                raise CodexProfileHold("CODEX_APP_SERVER_CLEANUP_FAILED") from exc


__all__ = ["CodexAppServerClient", "ProcessFactory"]
