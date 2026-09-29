"""Synthetic xAI OAuth contract. Every HTTP request is handled by MockTransport."""

import asyncio
import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from tests.unit.models.test_oauth_http_rejection import completed_event
from tests.unit.models.test_oauth_receive_observation import model_request

from thoth.adapters.models.local_credentials import (
    account_connections,
    local_secret,
    register_api_key,
)
from thoth.adapters.models.xai_broker import (
    XaiAuthBroker,
    _Login,  # pyright: ignore[reportPrivateUsage]
)
from thoth.adapters.models.xai_catalog import ThothXaiOAuthCatalog
from thoth.adapters.models.xai_model import XaiWorkspaceModel
from thoth.adapters.models.xai_oauth import XaiSession
from thoth.adapters.models.xai_profile import XaiCredential, XaiProfileHold
from thoth.adapters.models.xai_responses import XaiResponsesExecutor
from thoth.domain.model_settings import ResolvedModelSettings
from thoth.ports.model import ModelTransportHold

pytestmark = pytest.mark.usefixtures("xai_http_guard")


def test_uninjected_auth_is_blocked_before_http(tmp_path: Path) -> None:
    with pytest.raises(AssertionError, match="XAI_TEST_REAL_HTTP_BLOCKED"):
        XaiAuthBroker(tmp_path).start_login()


def test_login_repr_never_contains_device_secret() -> None:
    login = _Login(
        "synthetic-id",
        "ABCD-EFGH",
        "https://grok.com/activate",
        time.time() + 30,
        1,
        "synthetic-private-device-code",
    )
    assert "synthetic-private-device-code" not in repr(login)


def test_explicit_real_sync_transport_is_blocked_before_request() -> None:
    real = httpx.HTTPTransport()
    with pytest.raises(AssertionError, match="XAI_TEST_REAL_HTTP_BLOCKED"):
        httpx.Client(transport=real)
    with pytest.raises(AssertionError, match="XAI_TEST_REAL_HTTP_BLOCKED"):
        real.handle_request(httpx.Request("GET", "https://auth.x.ai/never-send"))


@pytest.mark.asyncio
async def test_explicit_real_async_transport_is_blocked_before_request() -> None:
    real = httpx.AsyncHTTPTransport()
    with pytest.raises(AssertionError, match="XAI_TEST_REAL_HTTP_BLOCKED"):
        httpx.AsyncClient(transport=real)
    with pytest.raises(AssertionError, match="XAI_TEST_REAL_HTTP_BLOCKED"):
        await real.handle_async_request(httpx.Request("GET", "https://api.x.ai/never-send"))


def test_unsupported_client_is_typed_and_sends_one_synthetic_start(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def reject(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(400, json={"error": "unauthorized_client"})

    with pytest.raises(XaiProfileHold, match="XAI_CLIENT_UNSUPPORTED"):
        XaiAuthBroker(tmp_path, transport=httpx.MockTransport(reject)).start_login()
    assert len(seen) == 1
    assert str(seen[0].url) == "https://auth.x.ai/oauth2/device/code"


@pytest.mark.asyncio
async def test_uninjected_model_is_blocked_before_http() -> None:
    class Session:
        def read(self) -> XaiSession:
            return XaiSession("synthetic", "grok-4.6")

    executor = XaiResponsesExecutor(Session())
    prepared = executor.prepare(
        "synthetic", {"type": "object"}, output_tokens=32, timeout_seconds=2
    )
    with pytest.raises(AssertionError, match="XAI_TEST_REAL_HTTP_BLOCKED"):
        await executor.dispatch(prepared)


def _device() -> dict[str, object]:
    return {
        "device_code": "private-device-code",
        "user_code": "ABCD-EFGH",
        "verification_uri": "https://grok.com/activate",
        "expires_in": 30,
        "interval": 1,
    }


def _wait_for(broker: XaiAuthBroker, state: str, timeout: float = 4) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = broker.login_status()
        if result["state"] == state:
            return result
        time.sleep(0.03)
    raise AssertionError(f"login did not reach {state}: {broker.login_status()}")


def test_pending_success_store_reopen_catalog_and_safe_dto(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path.endswith("/device/code"):
            return httpx.Response(200, json=_device())
        if len(calls) == 2:
            return httpx.Response(400, json={"error": "authorization_pending"})
        return httpx.Response(
            200,
            json={
                "access_token": "private-access",
                "refresh_token": "private-refresh",
                "expires_in": 3600,
            },
        )

    broker = XaiAuthBroker(tmp_path, transport=httpx.MockTransport(handler))
    started = broker.start_login()
    assert started["state"] == "PENDING"
    assert "private-device-code" not in json.dumps(started)
    assert "private-access" not in json.dumps(started)
    _wait_for(broker, "CONNECTED")
    assert broker.cancel_login(str(started["login_id"]))["state"] == "CONNECTED"
    reopened = XaiAuthBroker(tmp_path, transport=httpx.MockTransport(handler))
    assert reopened.status()["execution_eligible"] is True
    assert reopened.execution_token() == "private-access"
    assert [
        option.model for option in ThothXaiOAuthCatalog(tmp_path, broker=reopened).options()
    ] == ["grok-4.6"]
    assert len(calls) == 3


@pytest.mark.parametrize(
    ("error", "state"),
    [("access_denied", "DENIED"), ("expired_token", "EXPIRED")],
)
def test_denied_and_expired_do_not_store_token(tmp_path: Path, error: str, state: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/device/code"):
            return httpx.Response(200, json=_device())
        return httpx.Response(400, json={"error": error})

    broker = XaiAuthBroker(tmp_path, transport=httpx.MockTransport(handler))
    broker.start_login()
    assert _wait_for(broker, state)["reason_code"]
    assert broker.profile.read() is None


def test_cancel_fences_late_token_and_untrusted_uri(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/device/code"):
            return httpx.Response(200, json=_device())
        entered.set()
        release.wait(2)
        return httpx.Response(
            200,
            json={
                "access_token": "late-access",
                "refresh_token": "late-refresh",
                "expires_in": 3600,
            },
        )

    broker = XaiAuthBroker(tmp_path, transport=httpx.MockTransport(handler))
    started = broker.start_login()
    assert entered.wait(3)
    broker.cancel_login(str(started["login_id"]))
    release.set()
    assert broker.login_status()["state"] == "CANCELLED"
    time.sleep(0.1)
    assert broker.profile.read() is None

    def bad(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={**_device(), "verification_uri": "https://evil.example"})

    with pytest.raises(XaiProfileHold, match="XAI_VERIFICATION_URI_UNTRUSTED"):
        XaiAuthBroker(tmp_path, transport=httpx.MockTransport(bad)).start_login()


def test_new_attempt_and_cancel_preserve_previous_credential_and_reject_wrong_id(
    tmp_path: Path,
) -> None:
    broker = XaiAuthBroker(
        tmp_path, transport=httpx.MockTransport(lambda _: httpx.Response(200, json=_device()))
    )
    prior = XaiCredential("old-access", "old-refresh", time.time() + 3600, "old-generation")
    with broker.profile.lock():
        broker.profile.set_generation(prior.generation)
        broker.profile.save(prior)
    started = broker.start_login()
    assert broker.execution_token() == "old-access"
    with pytest.raises(XaiProfileHold, match="XAI_LOGIN_NOT_FOUND"):
        broker.login_status("another-attempt")
    broker.cancel_login(str(started["login_id"]))
    assert broker.execution_token() == "old-access"
    assert broker.profile.read() == prior


def test_refresh_429_stays_rate_limited_and_blocks_dispatch(tmp_path: Path) -> None:
    calls = 0

    def limited(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, json={"error": "rate_limit"})

    broker = XaiAuthBroker(tmp_path, transport=httpx.MockTransport(limited))
    expired = XaiCredential("old-access", "old-refresh", time.time() + 1, "gen")
    with broker.profile.lock():
        broker.profile.set_generation(expired.generation)
        broker.profile.save(expired)
    with pytest.raises(XaiProfileHold, match="XAI_REFRESH_RATE_LIMITED"):
        broker.execution_credential()
    assert calls == 1
    with pytest.raises(XaiProfileHold, match="XAI_REFRESH_RATE_LIMITED"):
        broker.execution_credential()
    assert calls == 1


def test_dispatch_gate_rejects_generation_rotation_before_first_send(tmp_path: Path) -> None:
    broker = XaiAuthBroker(tmp_path)
    prior = XaiCredential("old-access", "old-refresh", time.time() + 3600, "old-gen")
    with broker.profile.lock():
        broker.profile.set_generation(prior.generation)
        broker.profile.save(prior)
    snapshot = broker.execution_credential()
    with broker.profile.lock():
        broker.profile.set_generation("new-gen")
        broker.profile.save(replace(prior, generation="new-gen"))
    with (
        pytest.raises(XaiProfileHold, match="XAI_AUTH_SNAPSHOT_CHANGED"),
        broker.dispatch_gate(snapshot),
    ):
        raise AssertionError("physical send should not start")


@pytest.mark.asyncio
async def test_generation_rotation_blocks_actual_responses_dispatch(tmp_path: Path) -> None:
    broker = XaiAuthBroker(tmp_path)
    prior = XaiCredential("old-access", "old-refresh", time.time() + 3600, "old-gen")
    with broker.profile.lock():
        broker.profile.set_generation(prior.generation)
        broker.profile.save(prior)
    snapshot = broker.execution_credential()
    sent: list[httpx.Request] = []

    def reply(wire: httpx.Request) -> httpx.Response:
        sent.append(wire)
        return httpx.Response(200)

    class SessionReader:
        def read(self) -> XaiSession:
            return XaiSession(snapshot.access_token, "grok-4.6")

    executor = XaiResponsesExecutor(
        SessionReader(),
        transport=httpx.MockTransport(reply),
        dispatch_guard=lambda: broker.dispatch_gate_async(snapshot),
    )
    prepared = executor.prepare(
        "synthetic", {"type": "object"}, output_tokens=32, timeout_seconds=3
    )
    with broker.profile.lock():
        broker.profile.set_generation("new-gen")
        broker.profile.save(replace(prior, generation="new-gen"))
    with pytest.raises(ModelTransportHold, match="XAI_AUTH_SNAPSHOT_CHANGED"):
        await executor.dispatch(prepared)
    assert sent == []


@pytest.mark.asyncio
async def test_concurrent_xai_dispatch_keeps_event_loop_responsive(tmp_path: Path) -> None:
    broker = XaiAuthBroker(tmp_path)
    saved = XaiCredential("synthetic-access", "synthetic-refresh", time.time() + 3600, "gen")
    with broker.profile.lock():
        broker.profile.set_generation(saved.generation)
        broker.profile.save(saved)
    snapshot = broker.execution_credential()
    entered, release, stop = asyncio.Event(), asyncio.Event(), asyncio.Event()
    sent = 0
    ticks = 0

    async def reply(_wire: httpx.Request) -> httpx.Response:
        nonlocal sent
        sent += 1
        if sent == 1:
            entered.set()
            await release.wait()
        event = {
            "type": "response.completed",
            "response": {
                "id": f"synthetic-{sent}",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": '{"status":"OK"}'},
                        ],
                    }
                ],
            },
        }
        return httpx.Response(200, content=b"data: " + json.dumps(event).encode() + b"\n\n")

    async def heartbeat() -> None:
        nonlocal ticks
        while not stop.is_set():
            ticks += 1
            await asyncio.sleep(0.01)

    class SessionReader:
        def read(self) -> XaiSession:
            return XaiSession(snapshot.access_token, "grok-4.6")

    executor = XaiResponsesExecutor(
        SessionReader(),
        transport=httpx.MockTransport(reply),
        dispatch_guard=lambda: broker.dispatch_gate_async(snapshot),
    )
    prepared = [
        executor.prepare("synthetic", {"type": "object"}, output_tokens=32, timeout_seconds=3)
        for _ in range(2)
    ]
    first = asyncio.create_task(executor.dispatch(prepared[0]))
    await asyncio.wait_for(entered.wait(), 2)
    second = asyncio.create_task(executor.dispatch(prepared[1]))
    pulse = asyncio.create_task(heartbeat())
    try:
        await asyncio.sleep(0.15)
        assert ticks >= 5 and not second.done()
        release.set()
        replies = await asyncio.wait_for(asyncio.gather(first, second), 3)
        assert len(replies) == 2 and sent == 2
    finally:
        release.set()
        stop.set()
        await pulse


def test_slow_down_and_wall_expiry_are_distinct(tmp_path: Path) -> None:
    polls = 0

    def slow(request: httpx.Request) -> httpx.Response:
        nonlocal polls
        if request.url.path.endswith("/device/code"):
            return httpx.Response(200, json=_device())
        polls += 1
        return httpx.Response(400, json={"error": "slow_down"})

    broker = XaiAuthBroker(tmp_path / "slow", transport=httpx.MockTransport(slow))
    started = broker.start_login()
    assert _wait_for(broker, "SLOW_DOWN")["reason_code"] is None
    time.sleep(0.2)
    assert polls == 1
    broker.cancel_login(str(started["login_id"]))

    def short(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={**_device(), "expires_in": 0.1})

    expired = XaiAuthBroker(tmp_path / "expired", transport=httpx.MockTransport(short))
    expired.start_login()
    assert _wait_for(expired, "EXPIRED")["reason_code"] == "XAI_AUTH_EXPIRED"


def test_refresh_rotation_cross_instance_lock_and_ambiguous_hold(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.path.endswith("/token")
        return httpx.Response(
            200,
            json={
                "access_token": "rotated-access",
                "refresh_token": "rotated-refresh",
                "expires_in": 3600,
            },
        )

    broker = XaiAuthBroker(tmp_path, transport=httpx.MockTransport(handler))
    with broker.profile.lock():
        broker.profile.set_generation("generation-1")
        broker.profile.save(XaiCredential("old", "old-refresh", time.time() - 1, "generation-1"))
    other = XaiAuthBroker(tmp_path, transport=httpx.MockTransport(handler))
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(broker.execution_token)
        b = pool.submit(other.execution_token)
        assert a.result() == b.result() == "rotated-access"
    assert calls == 1
    assert broker.profile.read().refresh_token == "rotated-refresh"  # type: ignore[union-attr]

    def unknown(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("synthetic ambiguous refresh")

    with broker.profile.lock():
        broker.profile.save(
            XaiCredential("old", "rotated-refresh", time.time() - 1, "generation-1")
        )
    held = XaiAuthBroker(tmp_path, transport=httpx.MockTransport(unknown))
    with pytest.raises(XaiProfileHold, match="XAI_REFRESH_OUTCOME_UNKNOWN"):
        held.execution_token()
    with pytest.raises(XaiProfileHold, match="XAI_REFRESH_OUTCOME_UNKNOWN"):
        held.execution_token()


def test_credential_reopens_in_another_process_and_workspace_is_isolated(tmp_path: Path) -> None:
    workspace = tmp_path / "first"
    broker = XaiAuthBroker(workspace)
    with broker.profile.lock():
        broker.profile.set_generation("generation-first")
        broker.profile.save(
            XaiCredential("secret-first", "refresh-first", time.time() + 3600, "generation-first")
        )
    assert XaiAuthBroker(tmp_path / "second").status()["execution_eligible"] is False
    script = (
        "import pathlib,sys,httpx; "
        "from thoth.adapters.models.xai_broker import XaiAuthBroker; "
        "httpx.Client=lambda *a,**k: (_ for _ in ()).throw(AssertionError('NETWORK_BLOCKED')); "
        "httpx.AsyncClient=lambda *a,**k: "
        "(_ for _ in ()).throw(AssertionError('NETWORK_BLOCKED')); "
        "b=XaiAuthBroker(pathlib.Path(sys.argv[1])); "
        "print('READY' if b.status()['execution_eligible'] else 'HOLD')"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(workspace)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "READY"


def test_api_key_and_oauth_routes_coexist_without_key_mutation(tmp_path: Path) -> None:
    register_api_key(provider="xai", model="grok-4.6", api_key="synthetic-api-key", root=tmp_path)
    broker = XaiAuthBroker(tmp_path)
    with broker.profile.lock():
        broker.profile.set_generation("synthetic-coexist")
        broker.profile.save(
            XaiCredential(
                "synthetic-access",
                "synthetic-refresh",
                time.time() + 3600,
                "synthetic-coexist",
            )
        )
    row = next(
        item
        for item in account_connections(
            {"connected": False},
            tmp_path,
            broker.status(),
        )
        if item["provider"] == "xai"
    )
    assert row["available_model_providers"] == ["xai", "xai-oauth"]
    assert row["has_key"] is True and row["oauth"] is True
    assert local_secret("xai", tmp_path) == "synthetic-api-key"


@pytest.mark.asyncio
async def test_explicit_route_uses_saved_auth_and_fake_responses(tmp_path: Path) -> None:
    broker = XaiAuthBroker(tmp_path)
    with broker.profile.lock():
        broker.profile.set_generation("generation-2")
        broker.profile.save(
            XaiCredential("workspace-access", "refresh", time.time() + 3600, "generation-2")
        )
    sent: list[httpx.Request] = []

    def response(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, content=completed_event())

    settings = ResolvedModelSettings(
        provider="xai-oauth",
        model="grok-4.6",
        reasoning_effort="high",
        source_by_field={"provider": "REQUEST", "model": "REQUEST", "reasoning_effort": "REQUEST"},
        capability_source="thoth-curated/senpi-ai-2026.9.26-xai-json",
        settings_digest="b" * 64,
    )
    result = await XaiWorkspaceModel(broker, transport=httpx.MockTransport(response)).structured(
        replace(model_request(), model_settings=settings)
    )
    assert result.model_id == "xai-oauth/grok-4.6"
    assert sent[0].headers["Authorization"] == "Bearer workspace-access"
    body = json.loads(sent[0].content)
    assert body["model"] == "grok-4.6"
    assert body["reasoning"] == {"effort": "high"}


@pytest.mark.asyncio
async def test_ambiguous_refresh_sends_no_model_request(tmp_path: Path) -> None:
    refresh_calls: list[httpx.Request] = []
    model_calls: list[httpx.Request] = []

    def uncertain(request: httpx.Request) -> httpx.Response:
        refresh_calls.append(request)
        raise httpx.ReadTimeout("synthetic timeout after possible refresh")

    def model(request: httpx.Request) -> httpx.Response:
        model_calls.append(request)
        return httpx.Response(200, content=completed_event())

    broker = XaiAuthBroker(tmp_path, transport=httpx.MockTransport(uncertain))
    with broker.profile.lock():
        broker.profile.set_generation("generation-uncertain")
        broker.profile.save(
            XaiCredential(
                "stale-access",
                "refresh",
                time.time() - 1,
                "generation-uncertain",
            )
        )
    settings = ResolvedModelSettings(
        provider="xai-oauth",
        model="grok-4.6",
        reasoning_effort=None,
        source_by_field={
            "provider": "REQUEST",
            "model": "REQUEST",
            "reasoning_effort": "TRANSPORT_DEFAULT",
        },
        capability_source="thoth-curated/senpi-ai-2026.9.26-xai-json",
        settings_digest="c" * 64,
    )
    route = XaiWorkspaceModel(broker, transport=httpx.MockTransport(model))
    request = replace(model_request(), model_settings=settings)
    with pytest.raises(XaiProfileHold, match="XAI_REFRESH_OUTCOME_UNKNOWN"):
        await route.structured(request)
    with pytest.raises(XaiProfileHold, match="XAI_REFRESH_OUTCOME_UNKNOWN"):
        await route.structured(request)
    assert len(refresh_calls) == 1
    assert model_calls == []
