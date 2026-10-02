"""Synthetic Codex auth/catalog owner; never touches the host Codex profile."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from tests.integration.storage_coverage_helpers import request, value

from thoth.adapters.models import codex_broker
from thoth.adapters.models.catalog import CodexModelCatalog
from thoth.adapters.models.codex_broker import CodexAuthBroker
from thoth.adapters.models.codex_http import CodexHttpExecutor, CodexLocalSession
from thoth.adapters.models.codex_profile import (
    PINNED_VERSION,
    PLATFORM_INTEGRITY,
    WRAPPER_INTEGRITY,
    CodexProfile,
    CodexProfileHold,
)
from thoth.apps.model_composition import create_codex_model
from thoth.apps.runtime import create_runtime
from thoth.domain.base import DomainModel
from thoth.domain.enums import ModelRole
from thoth.domain.model import ContextPack, ModelRequest
from thoth.domain.model_dispatch import OAuthSession
from thoth.ports.model import ModelExecutionHold, ModelTransportHold


class ProbeOutput(DomainModel):
    status: str
    message: str


def _request() -> ModelRequest[ProbeOutput]:
    return ModelRequest(
        role=ModelRole.USER_EXPLAINER,
        project_id="project:synthetic",
        cutoff_at=datetime.now(UTC),
        context_pack=ContextPack(
            case_id="case:synthetic",
            project_id="project:synthetic",
            object_id="object:synthetic",
            problem="Probe the bound model",
            evidence=(),
            criteria=(),
            sufficiency=None,
            input_head_set_digest="0" * 64,
        ),
        output_model=ProbeOutput,
        prompt_version="probe.v1",
        model_policy_ref="model-policy:synthetic",
        max_output_tokens=256,
    )


def _package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "tools" / "node_modules" / "@openai" / "codex"
    platform = root / "node_modules" / "@openai" / "codex-win32-x64"
    native = platform / "vendor" / "x86_64-pc-windows-msvc" / "bin" / "codex.exe"
    native.parent.mkdir(parents=True)
    native.write_bytes(b"synthetic-native-not-executed")
    (root / "package.json").write_text(
        json.dumps({"name": "@openai/codex", "version": "0.157.1"}), encoding="utf-8"
    )
    (platform / "package.json").write_text(
        json.dumps({"name": "@openai/codex", "version": "0.157.1-win32-x64"}),
        encoding="utf-8",
    )
    (tmp_path / "tools" / "node_modules" / ".package-lock.json").write_text(
        json.dumps(
            {
                "packages": {
                    "node_modules/@openai/codex": {"integrity": WRAPPER_INTEGRITY},
                    "node_modules/@openai/codex/node_modules/@openai/codex-win32-x64": {
                        "integrity": PLATFORM_INTEGRITY
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("THOTH_CODEX_PACKAGE_ROOT", str(root))
    return native


def _token(account: str = "account:synthetic", *, expires_in_seconds: int = 3600) -> str:
    payload = {
        "exp": int((datetime.now(UTC) + timedelta(seconds=expires_in_seconds)).timestamp()),
        "chatgpt_account_id": account,
    }
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return "header." + encoded + ".signature"


def _auth(
    profile: CodexProfile,
    *,
    old: bool = False,
    account: str = "account:synthetic",
    expires_in_seconds: int = 3600,
) -> None:
    profile.auth_path.write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "OPENAI_API_KEY": None,
                "tokens": {
                    "access_token": _token(account, expires_in_seconds=expires_in_seconds),
                    "refresh_token": "synthetic-refresh-do-not-log",
                    "account_id": account,
                },
                "last_refresh": (
                    datetime.now(UTC) - timedelta(hours=1) if old else datetime.now(UTC)
                ).isoformat(),
            }
        ),
        encoding="utf-8",
    )


class FakeAppServer:
    def __init__(self, profile: CodexProfile, *, refresh: bool = True) -> None:
        self.profile = profile
        self.refresh = refresh
        self.methods: list[str] = []
        self.refresh_requests: list[bool] = []
        self.routing_origin = "https://chatgpt.com"
        self.routing_override: str | None = "NO_CONSTRAINT"
        self.account_id = "account:synthetic"
        self.closed = False

    def call(self, method: str, params: dict[str, object]) -> dict[str, object]:
        self.methods.append(method)
        if method == "account/read":
            assert set(params) == {"refreshToken"}
            refresh = params["refreshToken"]
            assert isinstance(refresh, bool)
            self.refresh_requests.append(refresh)
            if self.refresh and refresh:
                _auth(self.profile, account=self.account_id)
            return {
                "account": {"type": "chatgpt"},
                "workspaceRouting": {
                    "chatgptAccountId": self.account_id,
                    "backendOrigin": self.routing_origin,
                    "accountRoutingOverride": self.routing_override,
                },
            }
        if method == "model/list":
            return {
                "data": [
                    {
                        "model": "synthetic-codex",
                        "hidden": False,
                        "isDefault": True,
                        "defaultReasoningEffort": "medium",
                        "supportedReasoningEfforts": [
                            {"reasoningEffort": "low"},
                            {"reasoningEffort": "medium"},
                        ],
                    }
                ],
                "nextCursor": None,
            }
        if method == "account/login/start":
            return {"type": "chatgpt", "authUrl": "https://example.test/login", "loginId": "id"}
        if method == "account/login/cancel":
            assert params == {"loginId": "id"}
            return {}
        raise AssertionError("thread/turn or other method must never reach fake App Server")

    def wait_for_login(self, login_id: str, *, timeout_seconds: float) -> bool:
        assert login_id == "id" and timeout_seconds > 0
        _auth(self.profile)
        return True

    def close(self) -> None:
        self.closed = True


def make_synthetic_broker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    refresh: bool = True,
) -> tuple[CodexAuthBroker, FakeAppServer]:
    _package(tmp_path, monkeypatch)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    broker: CodexAuthBroker | None = None
    server: FakeAppServer | None = None

    def factory(profile: CodexProfile, _identity: object) -> FakeAppServer:
        nonlocal server
        server = FakeAppServer(profile, refresh=refresh)
        return server

    broker = CodexAuthBroker(
        workspace,
        client_factory=factory,
        version_runner=lambda _: PINNED_VERSION,
        browser_opener=lambda _: True,
    )
    identity = broker.profile.executable(version_runner=lambda _: PINNED_VERSION)
    broker.profile.prepare_login(identity)
    _auth(broker.profile, old=True, expires_in_seconds=60 if not refresh else 3600)
    state = broker.state(force=True)
    assert server is not None
    if refresh:
        assert state.execution_eligible is True
    return broker, server


def test_isolated_auth_catalog_and_restart_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    desktop = tmp_path / "desktop" / "auth.json"
    desktop.parent.mkdir()
    desktop.write_bytes(b"desktop-sentinel")
    monkeypatch.setenv("CODEX_HOME", str(desktop.parent))
    broker, server = make_synthetic_broker(tmp_path, monkeypatch)
    state = broker.state()
    assert state.connected and state.execution_eligible
    assert state.public()["execution_verified"] is False
    assert CodexModelCatalog(broker.profile.workspace, broker=broker).options()[0].model == (
        "synthetic-codex"
    )
    session = broker.session(None)
    assert session.model == "synthetic-codex" and session.account_id == "account:synthetic"
    assert set(server.methods) == {"account/read", "model/list"}
    assert desktop.read_bytes() == b"desktop-sentinel"
    monkeypatch.delenv("THOTH_CODEX_PACKAGE_ROOT")
    restarted = CodexAuthBroker(
        broker.profile.workspace,
        client_factory=lambda profile, _identity: FakeAppServer(profile),
        version_runner=lambda _: PINNED_VERSION,
    )
    assert restarted.state(force=True).execution_eligible is True
    assert desktop.read_bytes() == b"desktop-sentinel"


def _documented_prefix_package(local: Path, *, version: str = "0.157.1") -> Path:
    prefix = local / "THOTH" / "tools" / "codex"
    root = prefix / "node_modules" / "@openai" / "codex"
    platform = root / "node_modules" / "@openai" / "codex-win32-x64"
    native = platform / "vendor" / "x86_64-pc-windows-msvc" / "bin" / "codex.exe"
    native.parent.mkdir(parents=True)
    native.write_bytes(b"synthetic-native-not-executed")
    (root / "package.json").write_text(
        json.dumps({"name": "@openai/codex", "version": version}), encoding="utf-8"
    )
    (platform / "package.json").write_text(
        json.dumps({"name": "@openai/codex", "version": "0.157.1-win32-x64"}), encoding="utf-8"
    )
    (prefix / "node_modules" / ".package-lock.json").write_text(
        json.dumps(
            {
                "packages": {
                    "node_modules/@openai/codex": {"integrity": WRAPPER_INTEGRITY},
                    "node_modules/@openai/codex/node_modules/@openai/codex-win32-x64": {
                        "integrity": PLATFORM_INTEGRITY
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    return native


@pytest.mark.skipif(os.name != "nt", reason="the pinned Codex package is Windows x64 only")
def test_documented_tools_prefix_is_found_without_env_or_global_codex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = tmp_path / "localappdata"
    native = _documented_prefix_package(local)
    monkeypatch.delenv("THOTH_CODEX_PACKAGE_ROOT", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("PATH", str(tmp_path / "empty-path"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    identity = CodexProfile.for_workspace(workspace).executable(
        version_runner=lambda _: PINNED_VERSION
    )
    assert identity.path == native.resolve()


@pytest.mark.skipif(os.name != "nt", reason="the pinned Codex package is Windows x64 only")
def test_documented_tools_prefix_still_rejects_an_unpinned_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = tmp_path / "localappdata"
    _documented_prefix_package(local, version="0.158.0")
    monkeypatch.delenv("THOTH_CODEX_PACKAGE_ROOT", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("PATH", str(tmp_path / "empty-path"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with pytest.raises(CodexProfileHold, match="CODEX_STANDALONE_PIN_UNAVAILABLE"):
        CodexProfile.for_workspace(workspace).executable(version_runner=lambda _: PINNED_VERSION)


def test_local_status_and_matching_cancel_never_refresh_or_erase_old_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, server = make_synthetic_broker(tmp_path, monkeypatch)
    auth_bytes = broker.profile.auth_path.read_bytes()
    server.methods.clear()
    assert broker.local_status().connected is True
    assert broker.login_status()["auth_state"] == "CONNECTED"
    assert server.methods == []
    started = broker.start_login()
    assert started["login_id"] == "id"
    assert broker.login_status("id")["login_state"] == "PENDING"
    with pytest.raises(CodexProfileHold, match="CODEX_LOGIN_NOT_FOUND"):
        broker.login_status("wrong")
    cancelled = broker.cancel_login("id")
    assert cancelled["login_state"] == "CANCELLED"
    assert broker.profile.auth_path.read_bytes() == auth_bytes
    assert "account/read" not in server.methods and "model/list" not in server.methods
    assert "account/login/cancel" in server.methods


def test_local_status_does_not_reuse_eligible_cache_after_binary_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, server = make_synthetic_broker(tmp_path, monkeypatch)
    server.methods.clear()
    native = broker.profile.executable(version_runner=lambda _: PINNED_VERSION).path
    native.write_bytes(b"changed-synthetic-native")
    state = broker.local_status()
    assert state.reason_code == "UPDATE_REVIEW_REQUIRED"
    assert state.execution_eligible is False
    assert server.methods == []


@pytest.mark.asyncio
async def test_new_process_explicit_settings_discovery_restores_saved_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, _server = make_synthetic_broker(tmp_path, monkeypatch)
    workspace = broker.profile.workspace
    monkeypatch.setitem(codex_broker._BROKERS, workspace.resolve(), broker)  # pyright: ignore[reportPrivateUsage]
    runtime = create_runtime(workspace)
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "restart-project",
                    {
                        "project_id": "project:restart",
                        "name": "Restart",
                        "cutoff_at": "2026-09-24T00:00:00Z",
                    },
                )
            )
        )
        saved = value(
            await runtime.bus.dispatch(
                request(
                    "model/settings/update",
                    "restart-selection",
                    {
                        "project_id": "project:restart",
                        "selection": {
                            "provider": "codex-oauth",
                            "model": "synthetic-codex",
                            "reasoning_effort": "medium",
                        },
                    },
                )
            )
        )
        assert saved["availability"] == "AVAILABLE"
    finally:
        runtime.close()
    auth_before_discovery = broker.profile.auth_path.read_bytes()

    script = """
import asyncio, json, sys
from pathlib import Path
from tests.unit.models.test_codex_isolated_broker import FakeAppServer
from tests.integration.storage_coverage_helpers import request, value
from thoth.adapters.models import codex_broker
from thoth.adapters.models.codex_broker import CodexAuthBroker
from thoth.adapters.models.codex_profile import PINNED_VERSION
from thoth.apps.runtime import create_runtime

workspace = Path(sys.argv[1])
broker = CodexAuthBroker(workspace,
    client_factory=lambda profile, _: FakeAppServer(profile),
    version_runner=lambda _: PINNED_VERSION)
codex_broker._BROKERS[workspace.resolve()] = broker
assert [o.model for o in broker.cached_state().options] == ['synthetic-codex']
assert broker.local_status().connected
assert broker._client is None
runtime = create_runtime(workspace)
async def inspect():
    return value(await runtime.bus.query(request(
        'model/settings/read', 'restart-discover', {'project_id': 'project:restart'})))
try:
    result = asyncio.run(inspect())
    assert result['availability'] == 'AVAILABLE'
    assert result['effective_settings']['provider'] == 'codex-oauth'
    assert result['effective_settings']['model'] == 'synthetic-codex'
    assert result['effective_settings']['reasoning_effort'] == 'medium'
    assert {option['model'] for option in result['model_options']} >= {'synthetic-codex'}
    assert broker._client is None  # the saved list serves; nothing asked the provider
    print(json.dumps({'discovery': 'AVAILABLE'}))
finally:
    runtime.close()
"""
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    reopened = subprocess.run(
        [sys.executable, "-B", "-c", script, str(workspace)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert reopened.returncode == 0, reopened.stderr
    observed = json.loads(reopened.stdout.strip().splitlines()[-1])
    assert observed["discovery"] == "AVAILABLE"
    assert broker.profile.auth_path.read_bytes() == auth_before_discovery


def test_refresh_metadata_without_new_snapshot_is_not_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, server = make_synthetic_broker(tmp_path, monkeypatch, refresh=False)
    assert server.methods == ["account/read"]
    assert server.refresh_requests == [True]
    assert broker.state().reason_code == "CODEX_REFRESH_NOT_CONFIRMED"
    assert broker.state().execution_eligible is False


def test_uncertain_refresh_never_repeats_true_and_recovers_only_after_file_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, server = make_synthetic_broker(tmp_path, monkeypatch, refresh=False)
    assert server.refresh_requests == [True]
    marker = broker.profile.refresh_hold_path.read_text(encoding="utf-8")
    assert "synthetic-refresh-do-not-log" not in marker and "header." not in marker
    assert broker.state(force=True).reason_code == "CODEX_REFRESH_OUTCOME_UNKNOWN"
    assert broker.local_status().reason_code == "CODEX_REFRESH_OUTCOME_UNKNOWN"
    assert server.refresh_requests == [True]

    servers: list[FakeAppServer] = []

    def factory(profile: CodexProfile, _identity: object) -> FakeAppServer:
        owned = FakeAppServer(profile, refresh=False)
        servers.append(owned)
        return owned

    restarted = CodexAuthBroker(
        broker.profile.workspace,
        client_factory=factory,
        version_runner=lambda _: PINNED_VERSION,
    )
    assert restarted.state(force=True).reason_code == "CODEX_REFRESH_OUTCOME_UNKNOWN"
    assert restarted.local_status().reason_code == "CODEX_REFRESH_OUTCOME_UNKNOWN"
    assert servers == []
    _auth(broker.profile)
    assert restarted.state(force=True).execution_eligible is True
    assert servers[0].refresh_requests == [False]
    assert not broker.profile.refresh_hold_path.exists()


def test_routing_mismatch_and_binary_drift_hold_before_inference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, server = make_synthetic_broker(tmp_path, monkeypatch)
    server.routing_origin = "https://unexpected.example"
    assert broker.state(force=True).reason_code == "CODEX_BACKEND_ROUTING_UNSUPPORTED"
    with pytest.raises(CodexProfileHold, match="CODEX_BACKEND_ROUTING_UNSUPPORTED"):
        broker.session("synthetic-codex")
    server.routing_origin = "https://chatgpt.com"
    native = broker.profile.executable(version_runner=lambda _: PINNED_VERSION).path
    native.write_bytes(b"changed-synthetic-native")
    assert broker.state(force=True).reason_code == "UPDATE_REVIEW_REQUIRED"


@pytest.mark.parametrize("override", ["us", "us_cr", None])
def test_unsupported_workspace_route_is_not_execution_eligible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, override: str | None
) -> None:
    broker, server = make_synthetic_broker(tmp_path, monkeypatch)
    server.routing_override = override
    state = broker.state(force=True)
    assert state.execution_eligible is False
    assert state.reason_code == "CODEX_BACKEND_ROUTING_UNSUPPORTED"


def test_same_account_disk_reload_is_distinct_from_failed_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, server = make_synthetic_broker(tmp_path, monkeypatch)
    server.refresh = False
    _auth(broker.profile)
    raw = json.loads(broker.profile.auth_path.read_text(encoding="utf-8"))
    raw["last_refresh"] = (datetime.now(UTC) - timedelta(seconds=20)).isoformat()
    raw["tokens"]["refresh_token"] = "synthetic-other-process-refresh"
    broker.profile.auth_path.write_text(json.dumps(raw), encoding="utf-8")
    state = broker.state(force=True)
    assert state.execution_eligible is True
    assert state.snapshot is not None
    assert state.snapshot.account_id == "account:synthetic"


def test_account_switch_requires_bounded_convergence_before_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, server = make_synthetic_broker(tmp_path, monkeypatch)
    server.account_id = "account:other"
    state = broker.state(force=True)
    assert state.execution_eligible is False
    assert state.reason_code == "CODEX_ACCOUNT_MISMATCH"
    assert server.methods[-1:] == ["account/read"]
    assert server.refresh_requests[-1:] == [False]


def test_expiring_token_concurrent_sessions_refresh_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, server = make_synthetic_broker(tmp_path, monkeypatch)
    _auth(broker.profile, old=True, expires_in_seconds=60)
    server.refresh_requests.clear()
    server.methods.clear()
    start = threading.Barrier(2)

    def read_session() -> OAuthSession:
        start.wait(timeout=3)
        return broker.session("synthetic-codex")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(read_session)
        second = pool.submit(read_session)
        sessions = (first.result(timeout=5), second.result(timeout=5))
    assert all(item.account_id == "account:synthetic" for item in sessions)
    assert server.refresh_requests.count(True) == 1
    # A request-time token refresh checks the account; it does not fetch the model list again.
    assert server.methods.count("model/list") == 0


def test_login_is_pending_until_validated_and_waited_owner_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _package(tmp_path, monkeypatch)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    servers: list[FakeAppServer] = []

    def factory(profile: CodexProfile, _identity: object) -> FakeAppServer:
        result = FakeAppServer(profile)
        servers.append(result)
        return result

    broker = CodexAuthBroker(
        workspace,
        client_factory=factory,
        version_runner=lambda _: PINNED_VERSION,
        browser_opener=lambda _: True,
    )
    started = broker.start_login(wait_for_completion=True, timeout_seconds=3)
    assert started["started"] is True
    assert started["execution_verified"] is False
    assert started["connection_state"] == "EXECUTION_UNVERIFIED"
    assert servers[0].closed is True
    assert not broker.profile.login_pending_path.exists()


def test_web_login_listener_has_bounded_owner_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, server = make_synthetic_broker(tmp_path, monkeypatch)
    started = broker.start_login(wait_for_completion=False)
    assert started["started"] is True and started["connected"] is False
    assert broker.profile.login_pending_path.is_file()
    broker.close()
    assert server.closed and not broker.profile.login_pending_path.exists()


def test_profile_lock_serializes_two_python_processes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, _server = make_synthetic_broker(tmp_path, monkeypatch)
    snippet = (
        "from pathlib import Path; import sys; "
        "from thoth.adapters.models.codex_profile import CodexProfile,CodexProfileHold; "
        "p=CodexProfile.for_workspace(Path(sys.argv[1])); "
        'exec(\'try:\\n with p.lock(timeout_seconds=0.2): print(\\"ACQUIRED\\")'
        "\\nexcept CodexProfileHold as e: print(str(e))')"
    )
    args = [sys.executable, "-B", "-c", snippet, str(broker.profile.workspace)]
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    with broker.profile.lock():
        blocked = subprocess.run(args, env=env, capture_output=True, text=True, timeout=3)
    allowed = subprocess.run(args, env=env, capture_output=True, text=True, timeout=3)
    assert blocked.returncode == allowed.returncode == 0
    assert blocked.stdout.strip() == "CODEX_PROFILE_BUSY"
    assert allowed.stdout.strip() == "ACQUIRED"


def test_two_local_runtime_owners_close_only_after_final_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, server = make_synthetic_broker(tmp_path, monkeypatch)
    workspace = broker.profile.workspace.resolve()
    monkeypatch.setitem(codex_broker._BROKERS, workspace, broker)  # pyright: ignore[reportPrivateUsage]
    first = codex_broker.retain_workspace_broker(workspace)
    second = codex_broker.retain_workspace_broker(workspace)
    assert first is second is broker
    codex_broker.release_workspace_broker(workspace)
    assert server.closed is False
    codex_broker.release_workspace_broker(workspace)
    assert server.closed is True


def test_cached_catalog_read_does_not_wait_on_slow_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, server = make_synthetic_broker(tmp_path, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    original = server.call

    def slow(method: str, params: dict[str, object]) -> dict[str, object]:
        if method == "account/read":
            entered.set()
            assert release.wait(3)
        return original(method, params)

    server.call = slow  # type: ignore[method-assign]
    worker = threading.Thread(target=lambda: broker.state(force=True), daemon=True)
    worker.start()
    assert entered.wait(3)
    started = time.monotonic()
    options = CodexModelCatalog(broker.profile.workspace, broker=broker).options()
    assert options and time.monotonic() - started < 0.25
    release.set()
    worker.join(timeout=3)
    assert not worker.is_alive()


@pytest.mark.asyncio
async def test_account_rotation_at_first_physical_send_sends_zero_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, _server = make_synthetic_broker(tmp_path, monkeypatch)
    sent: list[httpx.Request] = []

    def reply(wire: httpx.Request) -> httpx.Response:
        sent.append(wire)
        return httpx.Response(200)

    executor = CodexHttpExecutor(
        CodexLocalSession(broker.profile.workspace, "synthetic-codex", broker=broker),
        transport=httpx.MockTransport(reply),
    )
    prepared = executor.prepare(
        "synthetic", {"type": "object"}, output_tokens=32, timeout_seconds=3
    )
    original_gate = broker.dispatch_gate_async

    @asynccontextmanager
    async def rotate_before_send(session: OAuthSession, *, expected_digest: str | None = None):
        _auth(broker.profile, account="account:changed")
        async with original_gate(session, expected_digest=expected_digest):
            yield

    monkeypatch.setattr(broker, "dispatch_gate_async", rotate_before_send)
    with pytest.raises(ModelTransportHold, match="OAUTH_AUTH_SNAPSHOT_CHANGED"):
        await executor.dispatch(prepared)
    assert sent == []


@pytest.mark.asyncio
async def test_same_account_file_change_between_prepare_and_dispatch_sends_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, server = make_synthetic_broker(tmp_path, monkeypatch)
    sent: list[httpx.Request] = []

    def reply(wire: httpx.Request) -> httpx.Response:
        sent.append(wire)
        return httpx.Response(200)

    executor = CodexHttpExecutor(
        CodexLocalSession(broker.profile.workspace, "synthetic-codex", broker=broker),
        transport=httpx.MockTransport(reply),
    )
    prepared = executor.prepare(
        "synthetic", {"type": "object"}, output_tokens=32, timeout_seconds=3
    )
    raw = json.loads(broker.profile.auth_path.read_text(encoding="utf-8"))
    raw["last_refresh"] = (datetime.now(UTC) - timedelta(seconds=20)).isoformat()
    broker.profile.auth_path.write_text(json.dumps(raw), encoding="utf-8")
    server.refresh_requests.clear()
    with pytest.raises(ModelExecutionHold, match="OAUTH_AUTH_SNAPSHOT_CHANGED_BEFORE_DISPATCH"):
        await executor.dispatch(prepared)
    assert sent == [] and True not in server.refresh_requests


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["same-token-digest", "pinned-binary"])
async def test_first_send_rejects_profile_digest_or_pin_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    broker, _server = make_synthetic_broker(tmp_path, monkeypatch)
    sent: list[httpx.Request] = []

    def reply(wire: httpx.Request) -> httpx.Response:
        sent.append(wire)
        return httpx.Response(200)

    executor = CodexHttpExecutor(
        CodexLocalSession(broker.profile.workspace, "synthetic-codex", broker=broker),
        transport=httpx.MockTransport(reply),
    )
    prepared = executor.prepare(
        "synthetic", {"type": "object"}, output_tokens=32, timeout_seconds=3
    )
    original_gate = broker.dispatch_gate_async

    @asynccontextmanager
    async def drift_before_send(session: OAuthSession, *, expected_digest: str | None = None):
        if mutation == "same-token-digest":
            raw = json.loads(broker.profile.auth_path.read_text(encoding="utf-8"))
            raw["last_refresh"] = (datetime.now(UTC) - timedelta(seconds=30)).isoformat()
            broker.profile.auth_path.write_text(json.dumps(raw), encoding="utf-8")
        else:
            native = broker.profile.executable(version_runner=lambda _: PINNED_VERSION).path
            native.write_bytes(b"changed-synthetic-native")
        async with original_gate(session, expected_digest=expected_digest):
            yield

    monkeypatch.setattr(broker, "dispatch_gate_async", drift_before_send)
    reason = (
        "OAUTH_AUTH_SNAPSHOT_CHANGED"
        if mutation == "same-token-digest"
        else "UPDATE_REVIEW_REQUIRED"
    )
    with pytest.raises(ModelTransportHold, match=reason):
        await executor.dispatch(prepared)
    assert sent == []


@pytest.mark.asyncio
async def test_two_valid_model_sends_do_not_force_token_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, server = make_synthetic_broker(tmp_path, monkeypatch)
    server.refresh_requests.clear()
    sent: list[httpx.Request] = []

    def reply(wire: httpx.Request) -> httpx.Response:
        sent.append(wire)
        event = {
            "type": "response.completed",
            "response": {
                "id": f"synthetic-{len(sent)}",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": '{"status":"OK","message":"ready"}'},
                        ],
                    }
                ],
            },
        }
        return httpx.Response(200, content=b"data: " + json.dumps(event).encode() + b"\n\n")

    model = create_codex_model(
        broker.profile.workspace, broker=broker, transport=httpx.MockTransport(reply)
    )
    first = await model.structured(_request())
    second = await model.structured(_request())
    assert first.output.status == second.output.status == "OK"
    assert len(sent) == 2
    assert True not in server.refresh_requests


@pytest.mark.asyncio
async def test_concurrent_codex_dispatch_keeps_event_loop_responsive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, _server = make_synthetic_broker(tmp_path, monkeypatch)
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

    executor = CodexHttpExecutor(
        CodexLocalSession(broker.profile.workspace, "synthetic-codex", broker=broker),
        transport=httpx.MockTransport(reply),
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


@pytest.mark.asyncio
async def test_unscoped_factory_consumes_exact_catalog_and_tool_free_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, _server = make_synthetic_broker(tmp_path, monkeypatch)
    sent: list[dict[str, object]] = []

    def response(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        event = {
            "type": "response.completed",
            "response": {
                "id": "synthetic-response",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": '{"status":"OK","message":"ready"}'}
                        ],
                    }
                ],
            },
        }
        return httpx.Response(200, content=b"data: " + json.dumps(event).encode() + b"\n\n")

    model = create_codex_model(
        broker.profile.workspace,
        model=None,
        broker=broker,
        transport=httpx.MockTransport(response),
    )
    result = await model.structured(_request())
    assert result.output == ProbeOutput(status="OK", message="ready")
    assert result.scripted is False
    assert len(sent) == 1
    assert sent[0]["model"] == "synthetic-codex"
    assert sent[0]["tools"] == [] and sent[0]["tool_choice"] == "none"
    assert sent[0]["parallel_tool_calls"] is False
    rejected = create_codex_model(
        broker.profile.workspace,
        model="unadvertised",
        broker=broker,
        transport=httpx.MockTransport(response),
    )
    with pytest.raises(ModelExecutionHold, match="OAUTH_MODEL_NOT_AVAILABLE"):
        await rejected.structured(_request())
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_account_identity_change_holds_before_any_http_post() -> None:
    calls: list[str] = []

    class SwitchingSession:
        def read(self) -> OAuthSession:
            calls.append("read")
            account = "account:first" if len(calls) == 1 else "account:changed"
            return OAuthSession("synthetic-token", account, "synthetic-codex")

    sent: list[bytes] = []

    def transport(request: httpx.Request) -> httpx.Response:
        sent.append(request.content)
        return httpx.Response(200)

    executor = CodexHttpExecutor(SwitchingSession(), transport=httpx.MockTransport(transport))
    prepared = executor.prepare(
        "synthetic", {"type": "object"}, output_tokens=64, timeout_seconds=3
    )
    with pytest.raises(ModelExecutionHold, match="OAUTH_ACCOUNT_CHANGED_BEFORE_DISPATCH"):
        await executor.dispatch(prepared)
    assert len(calls) == 2
    assert sent == []
