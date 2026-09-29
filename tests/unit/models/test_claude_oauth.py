"""Claude PKCE lifecycle uses only synthetic tokens and loopback callbacks."""

import http.client
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from thoth.adapters.models.claude_catalog import ClaudeOAuthCatalog
from thoth.adapters.models.claude_oauth import (
    ClaudeOAuthBroker,
    ClaudeOAuthHold,
    broker_for_workspace,
    close_workspace_broker,
    release_workspace_broker,
    retain_workspace_broker,
)
from thoth.adapters.models.claude_profile import ClaudeCredential

pytestmark = pytest.mark.usefixtures("xai_http_guard")


def token_reply(request: httpx.Request) -> httpx.Response:
    assert str(request.url) == "https://platform.claude.com/v1/oauth/token"
    return httpx.Response(
        200,
        json={
            "access_token": "synthetic-access",
            "refresh_token": "synthetic-refresh",
            "expires_in": 3600,
        },
    )


def wait_state(broker: ClaudeOAuthBroker, state: str, timeout: float = 5) -> dict[str, object]:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        status = broker.login_status()
        if status["login_state"] == state:
            return status
        time.sleep(0.03)
    raise AssertionError(f"login did not reach {state}: {broker.login_status()}")


def wait_cleanup(broker: ClaudeOAuthBroker, timeout: float = 5) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if broker.login_status()["cleanup_confirmed"] is True:
            return
        time.sleep(0.03)
    raise AssertionError("callback/worker cleanup not confirmed")


def test_unregistered_client_holds_without_network(tmp_path: Path) -> None:
    broker = ClaudeOAuthBroker(tmp_path)
    assert broker.status()["execution_eligible"] is False
    with pytest.raises(ClaudeOAuthHold, match="CLAUDE_CLIENT_REGISTRATION_REQUIRED"):
        broker.start_login()


def test_manual_pkce_success_reopen_and_cancel_preserves_connected_auth(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return token_reply(request)

    broker = ClaudeOAuthBroker(
        tmp_path,
        client_id="synthetic-client",
        transport=httpx.MockTransport(handler),
        browser_opener=urls.append,
        preferred_port=0,
    )
    try:
        started = broker.start_login()
        assert started["account_provider"] == "anthropic"
        assert started["auth_method"] == "claude_pkce"
        assert started["route"] == "claude-oauth"
        assert started["login_state"] == "PENDING"
        assert started["auth_state"] == "DISCONNECTED"
        assert cast(dict[str, object], started["capabilities"])["manual_complete"] is True
        assert urls == [started["authorization_url"]]
        params = parse_qs(urlsplit(urls[0]).query)
        assert params["code_challenge_method"] == ["S256"]
        state = params["state"][0]
        assert state not in repr(broker._attempt)  # pyright: ignore[reportPrivateUsage]
        assert "synthetic-access" not in json.dumps(started)
        assert broker.login_status(str(started["login_id"]))["login_state"] == "PENDING"
        assert calls == []
        with pytest.raises(ClaudeOAuthHold, match="CLAUDE_LOGIN_RESPONSE_INVALID"):
            broker.submit_login_response(str(started["login_id"]), "wrong-code#wrong-state")
        with pytest.raises(ClaudeOAuthHold, match="CLAUDE_LOGIN_RESPONSE_INVALID"):
            broker.submit_login_response(
                str(started["login_id"]),
                f"http://wrong@localhost:{urlsplit(params['redirect_uri'][0]).port}/callback"
                f"?code=synthetic&state={state}",
            )
        assert calls == []
        accepted = broker.submit_login_response(str(started["login_id"]), "code-synthetic#" + state)
        assert accepted["login_state"] in {"PENDING", "CONNECTED"}
        with pytest.raises(ClaudeOAuthHold, match="CLAUDE_LOGIN_NOT_PENDING"):
            broker.submit_login_response(str(started["login_id"]), "code-synthetic#" + state)
        done = wait_state(broker, "CONNECTED")
        wait_cleanup(broker)
        assert done["auth_state"] == "CONNECTED"
        assert done["execution_eligible"] is True
        assert "synthetic-access" not in json.dumps(done)
        assert len(calls) == 1
        assert broker.profile.read().access_token == "synthetic-access"  # type: ignore[union-attr]
        reopened = ClaudeOAuthBroker(tmp_path, client_id="synthetic-client")
        assert reopened.status()["auth_state"] == "CONNECTED"
        later = broker.start_login()
        cancelled = broker.cancel_login(str(later["login_id"]))
        assert cancelled["login_state"] == "CANCELLED"
        assert cancelled["auth_state"] == "CONNECTED"
        assert broker.profile.read().access_token == "synthetic-access"  # type: ignore[union-attr]
    finally:
        broker.close()


def test_callback_port_fallback_state_fence_and_one_exchange(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return token_reply(request)

    broker = ClaudeOAuthBroker(
        tmp_path,
        client_id="synthetic-client",
        transport=httpx.MockTransport(handler),
        preferred_port=53692,
    )
    original = broker._listen  # pyright: ignore[reportPrivateUsage]

    def collision(attempt: object, port: int):
        if port == 53692:
            raise OSError(10048, "synthetic occupied port")
        return original(attempt, port)  # type: ignore[arg-type]

    monkeypatch.setattr(broker, "_listen", collision)
    try:
        started = broker.start_login()
        assert started["manual_response_required"] is False
        auth = parse_qs(urlsplit(str(started["authorization_url"])).query)
        redirect = urlsplit(auth["redirect_uri"][0])
        assert redirect.port != 53692
        connection = http.client.HTTPConnection("localhost", redirect.port, timeout=2)
        try:
            connection.request(
                "GET",
                "/callback?code=synthetic&state=wrong",
                headers={"Host": f"localhost:{redirect.port}"},
            )
            rejected = connection.getresponse()
            assert rejected.status == 400
            rejected.read()
            assert calls == []
            connection.request(
                "GET",
                "/callback?error=access_denied&state=wrong",
                headers={"Host": f"localhost:{redirect.port}"},
            )
            denied_foreign = connection.getresponse()
            assert denied_foreign.status == 400
            denied_foreign.read()
            assert broker.login_status()["login_state"] == "PENDING"
            connection.request(
                "GET",
                "/callback?code=synthetic&state=" + auth["state"][0],
                headers={"Host": f"localhost:{redirect.port}"},
            )
            accepted = connection.getresponse()
            assert accepted.status == 202
            accepted.read()
        finally:
            connection.close()
        assert wait_state(broker, "CONNECTED")["auth_state"] == "CONNECTED"
        wait_cleanup(broker)
        assert len(calls) == 1
    finally:
        broker.close()


def test_full_port_block_falls_back_to_manual_redirect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker = ClaudeOAuthBroker(
        tmp_path,
        client_id="synthetic-client",
        transport=httpx.MockTransport(token_reply),
    )

    def blocked(_attempt: object, _port: int):
        raise OSError(10048, "synthetic all ports occupied")

    monkeypatch.setattr(broker, "_listen", blocked)
    try:
        started = broker.start_login()
        assert started["manual_response_required"] is True
        auth = parse_qs(urlsplit(str(started["authorization_url"])).query)
        response = auth["redirect_uri"][0] + "?code=synthetic&state=" + auth["state"][0]
        broker.submit_login_response(str(started["login_id"]), response)
        assert wait_state(broker, "CONNECTED")["connected"] is True
    finally:
        broker.close()


def test_expired_attempt_and_late_manual_response_send_zero_token_requests(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return token_reply(request)

    broker = ClaudeOAuthBroker(
        tmp_path,
        client_id="synthetic-client",
        transport=httpx.MockTransport(handler),
        preferred_port=0,
        lifetime_seconds=0.15,
    )
    try:
        started = broker.start_login()
        assert wait_state(broker, "EXPIRED")["reason_code"] == "CLAUDE_LOGIN_EXPIRED"
        with pytest.raises(ClaudeOAuthHold, match="CLAUDE_LOGIN_NOT_PENDING"):
            broker.submit_login_response(str(started["login_id"]), "late-code")
        assert calls == []
        assert broker.profile.read() is None
    finally:
        broker.close()


def test_cancel_during_token_exchange_fences_late_success(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        entered.set()
        release.wait(3)
        return token_reply(request)

    broker = ClaudeOAuthBroker(
        tmp_path,
        client_id="synthetic-client",
        transport=httpx.MockTransport(handler),
        preferred_port=0,
    )
    try:
        started = broker.start_login()
        broker.submit_login_response(str(started["login_id"]), "synthetic-code")
        assert entered.wait(3)
        assert broker.cancel_login(str(started["login_id"]))["login_state"] == "CANCELLED"
        release.set()
        time.sleep(0.15)
        assert broker.profile.read() is None
        assert broker.login_status()["login_state"] == "CANCELLED"
    finally:
        release.set()
        broker.close()


def test_cross_instance_refresh_rotation_and_unknown_outcome_hold(tmp_path: Path) -> None:
    calls = 0

    def rotated(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert json.loads(request.content)["grant_type"] == "refresh_token"
        return httpx.Response(
            200,
            json={
                "access_token": "rotated-access",
                "refresh_token": "rotated-refresh",
                "expires_in": 3600,
            },
        )

    broker = ClaudeOAuthBroker(
        tmp_path, client_id="synthetic-client", transport=httpx.MockTransport(rotated)
    )
    with broker.profile.lock():
        broker.profile.save(
            ClaudeCredential(
                "old-access",
                "old-refresh",
                time.time() - 1,
                "generation-1",
                broker.profile.profile_id,
            )
        )
    assert broker.status()["auth_state"] == "REFRESH_REQUIRED"
    assert calls == 0
    other = ClaudeOAuthBroker(
        tmp_path, client_id="synthetic-client", transport=httpx.MockTransport(rotated)
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(broker.execution_session)
        two = pool.submit(other.execution_session)
        assert one.result().access_token == two.result().access_token == "rotated-access"
    assert calls == 1
    assert broker.profile.read().refresh_token == "rotated-refresh"  # type: ignore[union-attr]

    def ambiguous(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("synthetic unknown refresh")

    with broker.profile.lock():
        broker.profile.save(
            ClaudeCredential(
                "old-access",
                "rotated-refresh",
                time.time() - 1,
                "generation-2",
                broker.profile.profile_id,
            )
        )
    held = ClaudeOAuthBroker(
        tmp_path, client_id="synthetic-client", transport=httpx.MockTransport(ambiguous)
    )
    with pytest.raises(ClaudeOAuthHold, match="CLAUDE_REFRESH_OUTCOME_UNKNOWN"):
        held.execution_session()
    with pytest.raises(ClaudeOAuthHold, match="CLAUDE_REFRESH_OUTCOME_UNKNOWN"):
        held.execution_session()
    assert broker.profile.read().state == "CLAUDE_REFRESH_OUTCOME_UNKNOWN"  # type: ignore[union-attr]


def test_generation_dispatch_gate_rejects_changed_profile(tmp_path: Path) -> None:
    broker = ClaudeOAuthBroker(tmp_path, client_id="synthetic-client")
    with broker.profile.lock():
        broker.profile.save(
            ClaudeCredential(
                "old-access",
                "refresh",
                time.time() + 3600,
                "generation-1",
                broker.profile.profile_id,
            )
        )
    snapshot = broker.execution_session()
    with broker.profile.lock():
        broker.profile.save(
            ClaudeCredential(
                "new-access",
                "new-refresh",
                time.time() + 3600,
                "generation-2",
                broker.profile.profile_id,
            )
        )
    with (
        pytest.raises(ClaudeOAuthHold, match="CLAUDE_AUTH_SNAPSHOT_CHANGED"),
        broker.dispatch_gate(snapshot),
    ):
        raise AssertionError("old credential must not enter dispatch")


def test_catalog_is_local_account_scoped_and_sonnet_45_effort_is_not_advertised(
    tmp_path: Path,
) -> None:
    first = ClaudeOAuthBroker(tmp_path / "first", client_id="synthetic-client")
    second = ClaudeOAuthBroker(tmp_path / "second", client_id="synthetic-client")
    with first.profile.lock():
        first.profile.save(
            ClaudeCredential(
                "first-access",
                "first-refresh",
                time.time() + 3600,
                "generation-first",
                first.profile.profile_id,
            )
        )
    available = ClaudeOAuthCatalog(tmp_path / "first", broker=first)
    isolated = ClaudeOAuthCatalog(tmp_path / "second", broker=second)
    assert available.defaults().provider is None
    assert isolated.options() == ()
    assert first.profile.profile_id != second.profile.profile_id
    options = available.options()
    assert len(options) == 1
    assert options[0].provider == "claude-oauth"
    assert options[0].model == "claude-sonnet-4-5-20250929"
    assert options[0].reasoning_efforts == ()


def test_two_processes_rotate_one_refresh_token_once(tmp_path: Path) -> None:
    broker = ClaudeOAuthBroker(tmp_path, client_id="synthetic-client")
    with broker.profile.lock():
        broker.profile.save(
            ClaudeCredential(
                "expired-access",
                "expired-refresh",
                time.time() - 1,
                "generation-before",
                broker.profile.profile_id,
            )
        )
    ready = tmp_path / "children-ready"
    go = tmp_path / "children-go.txt"
    sends = tmp_path / "refresh-sends"
    ready.mkdir()
    sends.mkdir()
    child = """
import os, pathlib, sys, time, uuid, httpx
from thoth.adapters.models.claude_oauth import ClaudeOAuthBroker
original = httpx.Client.__init__
def guarded(self, *args, **kwargs):
    if type(kwargs.get('transport')) is not httpx.MockTransport:
        raise AssertionError('CLAUDE_TEST_LIVE_HTTP_BLOCKED')
    return original(self, *args, **kwargs)
httpx.Client.__init__ = guarded
root, ready, go, sends = map(pathlib.Path, sys.argv[1:])
def fake(request):
    (sends / (uuid.uuid4().hex + '.txt')).write_text('refresh\\n', encoding='utf-8')
    return httpx.Response(200, json={
        'access_token':'rotated-access', 'refresh_token':'rotated-refresh',
        'expires_in':3600,
    })
broker = ClaudeOAuthBroker(root, client_id='synthetic-client', transport=httpx.MockTransport(fake))
(ready / (str(os.getpid()) + '.txt')).write_text('ready\\n', encoding='utf-8')
end = time.monotonic() + 8
while not go.exists() and time.monotonic() < end:
    time.sleep(0.01)
if not go.exists():
    raise RuntimeError('synthetic start barrier expired')
print(broker.execution_session().generation)
"""
    env = dict(os.environ)
    for key in ("HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "CODEX_HOME"):
        env[key] = str(tmp_path / "isolated-home")
    children = [
        subprocess.Popen(
            [sys.executable, "-c", child, str(tmp_path), str(ready), str(go), str(sends)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        for _ in range(2)
    ]
    try:
        end = time.monotonic() + 8
        while time.monotonic() < end:
            if len(tuple(ready.iterdir())) == 2:
                break
            exited = [process for process in children if process.poll() is not None]
            if exited:
                details = [process.communicate(timeout=1) for process in exited]
                raise AssertionError(f"synthetic child exited before barrier: {details}")
            time.sleep(0.02)
        else:
            raise AssertionError(
                f"two child processes did not reach barrier: {tuple(ready.iterdir())!r}"
            )
        go.write_text("go\n", encoding="utf-8")
        outcomes = [process.communicate(timeout=12) for process in children]
        assert all(process.returncode == 0 for process in children), outcomes
        generations = {stdout.strip() for stdout, _stderr in outcomes}
        assert len(generations) == 1 and "generation-before" not in generations
        sent = tuple(sends.iterdir())
        assert len(sent) == 1
        assert sent[0].read_text(encoding="utf-8").splitlines() == ["refresh"]
        assert broker.profile.read().refresh_token == "rotated-refresh"  # type: ignore[union-attr]
    finally:
        for process in children:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)


def test_workspace_broker_lifetime_is_reference_counted(tmp_path: Path) -> None:
    first = retain_workspace_broker(tmp_path)
    second = retain_workspace_broker(tmp_path)
    assert first is second is broker_for_workspace(tmp_path)
    with pytest.raises(ClaudeOAuthHold, match="CLAUDE_BROKER_RUNTIME_OWNED"):
        close_workspace_broker(tmp_path)
    release_workspace_broker(tmp_path)
    assert broker_for_workspace(tmp_path) is first
    release_workspace_broker(tmp_path)
    assert broker_for_workspace(tmp_path) is not first
    close_workspace_broker(tmp_path)
