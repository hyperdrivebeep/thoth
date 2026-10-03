"""The server finishes a Codex login itself: settles the attempt, loads the list once."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import pytest
from tests.unit.models.test_codex_isolated_broker import (
    FakeAppServer,
    _auth,  # pyright: ignore[reportPrivateUsage]
    _package,  # pyright: ignore[reportPrivateUsage]
)

from thoth.adapters.models.codex_broker import CodexAuthBroker
from thoth.adapters.models.codex_profile import PINNED_VERSION, CodexProfile, CodexProfileHold


class FailingListServer(FakeAppServer):
    def call(self, method: str, params: dict[str, object]) -> dict[str, object]:
        if method == "model/list":
            self.methods.append(method)
            raise CodexProfileHold("CATALOG_UNAVAILABLE")
        return super().call(method, params)


def make_broker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    server_type: type[FakeAppServer] = FakeAppServer,
    interval: float = 0.01,
) -> tuple[CodexAuthBroker, list[FakeAppServer]]:
    _package(tmp_path, monkeypatch)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    servers: list[FakeAppServer] = []

    def factory(profile: CodexProfile, _identity: object) -> FakeAppServer:
        server = server_type(profile)
        servers.append(server)
        return server

    broker = CodexAuthBroker(
        workspace,
        client_factory=factory,
        version_runner=lambda _: PINNED_VERSION,
        browser_opener=lambda _: True,
        login_watch_interval=interval,
    )
    return broker, servers


def until(condition: Callable[[], bool], seconds: float = 10.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.01)
    return False


def list_calls(servers: list[FakeAppServer]) -> int:
    return sum(server.methods.count("model/list") for server in servers)


def test_finished_login_settles_the_attempt_and_loads_the_list_once_without_the_screen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, servers = make_broker(tmp_path, monkeypatch)
    started = broker.start_login()
    login_id = str(started["login_id"])
    assert broker.login_status(login_id)["login_state"] == "PENDING"
    assert list_calls(servers) == 0
    _auth(broker.profile)  # the browser sign-in finished; nobody polls login_status

    assert until(lambda: list_calls(servers) == 1)
    assert until(lambda: broker.login_status(login_id)["login_state"] == "CONNECTED")
    done = broker.login_status(login_id)
    assert done["auth_state"] == "CONNECTED" and done["catalog_state"] == "AVAILABLE"
    assert done["execution_eligible"] is True and done["catalog_refresh"] == "DONE"
    assert not broker.profile.login_pending_path.exists()
    for _ in range(5):  # repeated status reads never reach the provider again
        assert broker.login_status(login_id)["login_state"] == "CONNECTED"
    assert list_calls(servers) == 1
    broker.close()


def test_status_polling_alone_also_finishes_the_login_and_asks_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, servers = make_broker(tmp_path, monkeypatch, interval=3600)
    login_id = str(broker.start_login()["login_id"])
    _auth(broker.profile)
    seen: list[str] = []

    def settled() -> bool:
        status = broker.login_status(login_id)
        seen.append(str(status["login_state"]))
        return status["login_state"] == "CONNECTED"

    assert until(settled)
    # while the list loads the attempt is still pending but the account is already shown connected
    assert seen[0] == "PENDING" or seen[0] == "CONNECTED"
    assert list_calls(servers) == 1
    broker.close()


def test_a_failed_list_load_keeps_the_login_connected_and_says_the_list_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, servers = make_broker(tmp_path, monkeypatch, server_type=FailingListServer)
    login_id = str(broker.start_login()["login_id"])
    _auth(broker.profile)
    assert until(lambda: broker.login_status(login_id)["login_state"] == "CONNECTED")
    status = broker.login_status(login_id)
    assert status["auth_state"] == "CONNECTED" and status["connected"] is True
    assert status["catalog_refresh"] == "FAILED" and status["execution_eligible"] is False
    assert list_calls(servers) == 1
    broker.close()


def test_a_cancelled_login_never_loads_a_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, servers = make_broker(tmp_path, monkeypatch)
    login_id = str(broker.start_login()["login_id"])
    broker.cancel_login(login_id)
    _auth(broker.profile)
    time.sleep(0.3)
    assert list_calls(servers) == 0
    broker.close()
