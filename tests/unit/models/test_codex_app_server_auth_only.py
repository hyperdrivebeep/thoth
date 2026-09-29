"""The THOTH App Server client accepts auth/catalog JSONL only."""

from __future__ import annotations

import hashlib
import json
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import cast

import pytest

from thoth.adapters.models import codex_app_server
from thoth.adapters.models.codex_app_server import AppServerProcessPort, CodexAppServerClient
from thoth.adapters.models.codex_profile import (
    PINNED_VERSION,
    PLATFORM_INTEGRITY,
    WRAPPER_INTEGRITY,
    CodexExecutableIdentity,
    CodexProfile,
    CodexProfileHold,
)


class _Output:
    def __init__(self) -> None:
        self.lines: queue.Queue[str | None] = queue.Queue()

    def __iter__(self) -> _Output:
        return self

    def __next__(self) -> str:
        value = self.lines.get(timeout=2)
        if value is None:
            raise StopIteration
        return value


class _Input:
    def __init__(self, process: _Process) -> None:
        self.process = process

    def write(self, line: str) -> int:
        message = json.loads(line)
        self.process.sent.append(message)
        identifier = message.get("id")
        if identifier is not None:
            result: dict[str, object] = (
                {"account": {"type": "chatgpt"}}
                if message["method"] == "account/read"
                else {"data": [], "nextCursor": None}
                if message["method"] == "model/list"
                else {
                    "type": "chatgpt",
                    "loginId": "login:synthetic",
                    "authUrl": "https://example.invalid/login",
                }
                if message["method"] == "account/login/start"
                else {}
            )
            self.process.stdout.lines.put(json.dumps({"id": identifier, "result": result}) + "\n")
        return len(line)

    def flush(self) -> None:
        pass


class _Process:
    def __init__(self, *, block_terminate: bool = False) -> None:
        self.sent: list[dict[str, object]] = []
        self.stdout = _Output()
        self.stdin = _Input(self)
        self.block_terminate = block_terminate
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return 0 if self.terminated or self.killed else None

    def terminate(self) -> None:
        self.terminated = True
        if not self.block_terminate:
            self.stdout.lines.put(None)

    def kill(self) -> None:
        self.killed = True
        self.stdout.lines.put(None)

    def wait(self, timeout: float | None = None) -> int:
        if self.block_terminate and not self.killed:
            raise subprocess.TimeoutExpired("fake-codex", 0 if timeout is None else timeout)
        return 0


def _client(
    tmp_path: Path, *, process: _Process | None = None
) -> tuple[CodexAppServerClient, _Process]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    profile = CodexProfile.for_workspace(workspace)
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"synthetic")
    identity = CodexExecutableIdentity(
        executable,
        tmp_path,
        PINNED_VERSION,
        hashlib.sha256(b"synthetic").hexdigest(),
        WRAPPER_INTEGRITY,
        PLATFORM_INTEGRITY,
    )
    profile.prepare_login(identity)
    owned = process or _Process()

    def factory(
        _identity: CodexExecutableIdentity, _profile: CodexProfile
    ) -> AppServerProcessPort:
        return cast(AppServerProcessPort, owned)

    return (
        CodexAppServerClient(
            profile,
            identity,
            process_factory=factory,
        ),
        owned,
    )


def test_app_server_client_sends_only_auth_catalog_after_handshake(tmp_path: Path) -> None:
    client, process = _client(tmp_path)
    assert client.call("account/read", {"refreshToken": True})["account"] == {
        "type": "chatgpt"
    }
    assert client.call("model/list", {"limit": 20})["data"] == []
    with pytest.raises(CodexProfileHold, match="CODEX_APP_SERVER_METHOD_FORBIDDEN"):
        client.call("thread/start", {})
    assert [row["method"] for row in process.sent] == [
        "initialize", "initialized", "account/read", "model/list"
    ]
    client.close()
    assert process.terminated


def test_notifications_cannot_extend_total_request_deadline(tmp_path: Path) -> None:
    client, _process = _client(tmp_path)
    client._timeout = 0.05  # pyright: ignore[reportPrivateUsage]
    keep_sending = threading.Event()

    def notifications() -> None:
        while not keep_sending.wait(0.005):
            client._messages.put({"method": "account/updated", "params": {}})  # pyright: ignore[reportPrivateUsage]

    producer = threading.Thread(target=notifications, daemon=True)
    producer.start()
    started = time.monotonic()
    with pytest.raises(CodexProfileHold, match="CODEX_APP_SERVER_TIMEOUT"):
        client._await(99)  # pyright: ignore[reportPrivateUsage]
    keep_sending.set()
    producer.join(timeout=1)
    assert time.monotonic() - started < 0.5


def test_close_kills_child_when_graceful_termination_times_out(tmp_path: Path) -> None:
    process = _Process(block_terminate=True)
    client, owned = _client(tmp_path, process=process)
    client.call("account/read", {"refreshToken": True})
    client.close()
    assert owned.terminated and owned.killed


def test_login_completion_notification_has_bounded_listener_owner(tmp_path: Path) -> None:
    client, process = _client(tmp_path)
    started = client.call("account/login/start", {"type": "chatgpt"})
    assert started["loginId"] == "login:synthetic"
    process.stdout.lines.put(
        json.dumps(
            {
                "method": "account/login/completed",
                "params": {"loginId": "login:synthetic", "success": True, "error": None},
            }
        )
        + "\n"
    )
    assert client.wait_for_login("login:synthetic", timeout_seconds=1) is True
    client.close()
    assert process.terminated


def test_spawn_isolates_child_cwd_and_auth_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _process = _client(tmp_path)
    observed: list[tuple[list[str], dict[str, object]]] = []

    def fake_popen(arguments: list[str], **kwargs: object) -> _Process:
        observed.append((arguments, kwargs))
        return _Process()

    monkeypatch.setenv("OPENAI_API_KEY", "host-secret-must-not-flow")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "desktop-sentinel"))
    monkeypatch.setattr(codex_app_server.subprocess, "Popen", fake_popen)
    codex_app_server._spawn(client.identity, client.profile)  # pyright: ignore[reportPrivateUsage]
    assert len(observed) == 1
    arguments, options = observed[0]
    assert arguments[1:] == ["app-server", "--listen", "stdio://"]
    assert options["cwd"] == client.profile.root
    env = cast(dict[str, str], options["env"])
    assert env["CODEX_HOME"] == str(client.profile.root)
    assert env["USERPROFILE"] == str(client.profile.root)
    assert "OPENAI_API_KEY" not in env
    assert "host-secret-must-not-flow" not in env.values()
