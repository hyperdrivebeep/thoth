"""Official App Server auth/catalog owner for a THOTH-only Codex profile."""

from __future__ import annotations

import asyncio
import atexit
import hmac
import threading
import time
import webbrowser
from collections.abc import AsyncGenerator, Callable, Generator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol, cast

from thoth.adapters.models.catalog import WIRE_EFFORTS, chatgpt_codex_model_id
from thoth.adapters.models.codex_app_server import CodexAppServerClient
from thoth.adapters.models.codex_profile import (
    CODEX_BACKEND_ORIGIN,
    CodexAuthSnapshot,
    CodexExecutableIdentity,
    CodexProfile,
    CodexProfileHold,
    VersionRunner,
    read_codex_executable_version,
)
from thoth.domain.model_dispatch import OAuthSession
from thoth.domain.model_settings import ModelOption, ModelSelection


class AppServerPort(Protocol):
    def call(self, method: str, params: dict[str, object]) -> dict[str, object]: ...
    def wait_for_login(self, login_id: str, *, timeout_seconds: float) -> bool: ...
    def close(self) -> None: ...


ClientFactory = Callable[[CodexProfile, CodexExecutableIdentity], AppServerPort]
BrowserOpener = Callable[[str], bool]


@dataclass(frozen=True)
class CodexBrokerState:
    connected: bool
    execution_eligible: bool
    reason_code: str
    options: tuple[ModelOption, ...] = ()
    default: ModelSelection = field(default_factory=lambda: ModelSelection(provider="codex-oauth"))
    snapshot: CodexAuthSnapshot | None = None

    def public(self) -> dict[str, object]:
        return {
            "provider": "codex-oauth",
            "connected": self.connected,
            "connection_state": (
                "EXECUTION_UNVERIFIED" if self.execution_eligible else self.reason_code
            ),
            "profile_mode": "THOTH_ISOLATED",
            "reason_code": "EXECUTION_UNVERIFIED" if self.execution_eligible else self.reason_code,
            "execution_eligible": self.execution_eligible,
            "execution_verified": False,
        }


class CodexAuthBroker:
    def __init__(
        self,
        workspace: Path,
        *,
        client_factory: ClientFactory | None = None,
        version_runner: VersionRunner = read_codex_executable_version,
        browser_opener: BrowserOpener = webbrowser.open,
    ) -> None:
        self.profile = CodexProfile.for_workspace(workspace)
        self._client_factory = client_factory or CodexAppServerClient
        self._version_runner = version_runner
        self._browser_opener = browser_opener
        self._client: AppServerPort | None = None
        self._lock = threading.RLock()
        self._cached: CodexBrokerState | None = None
        self._cached_at = 0.0
        self._pending_login_id: str | None = None
        self._login_timer: threading.Timer | None = None

    def _client_for(self, identity: CodexExecutableIdentity) -> AppServerPort:
        if self._client is None:
            self._client = self._client_factory(self.profile, identity)
        return self._client

    @staticmethod
    def _routing(account: dict[str, object], snapshot: CodexAuthSnapshot) -> None:
        routing = account.get("workspaceRouting")
        if routing is None:
            return
        if not isinstance(routing, dict):
            raise CodexProfileHold("CODEX_BACKEND_ROUTING_UNSUPPORTED")
        routed = cast(dict[str, object], routing)
        expected_keys = {"chatgptAccountId", "backendOrigin", "accountRoutingOverride"}
        if set(routed) != expected_keys:
            raise CodexProfileHold("CODEX_BACKEND_ROUTING_UNSUPPORTED")
        account_id = routed.get("chatgptAccountId")
        if account_id != snapshot.account_id:
            raise CodexProfileHold("CODEX_ACCOUNT_MISMATCH")
        origin = routed.get("backendOrigin")
        if (
            origin != CODEX_BACKEND_ORIGIN
            or routed.get("accountRoutingOverride") != "NO_CONSTRAINT"
        ):
            raise CodexProfileHold("CODEX_BACKEND_ROUTING_UNSUPPORTED")

    @staticmethod
    def _models(client: AppServerPort) -> tuple[tuple[ModelOption, ...], str | None]:
        options: list[ModelOption] = []
        default: str | None = None
        cursor: str | None = None
        for _ in range(10):
            params: dict[str, object] = {"limit": 100, "includeHidden": False}
            if cursor is not None:
                params["cursor"] = cursor
            result = client.call("model/list", params)
            data = result.get("data")
            if not isinstance(data, list):
                raise CodexProfileHold("CATALOG_UNAVAILABLE")
            for raw in cast(list[object], data):
                if not isinstance(raw, dict):
                    raise CodexProfileHold("CATALOG_UNAVAILABLE")
                entry = cast(dict[str, object], raw)
                model = chatgpt_codex_model_id(entry.get("model"))
                if model is None or entry.get("hidden") is True:
                    continue
                levels = entry.get("supportedReasoningEfforts")
                if not isinstance(levels, list):
                    raise CodexProfileHold("CATALOG_UNAVAILABLE")
                efforts = tuple(
                    effort
                    for item in cast(list[object], levels)
                    if isinstance(item, dict)
                    and isinstance(
                        (effort := cast(dict[str, object], item).get("reasoningEffort")), str
                    )
                    and effort in WIRE_EFFORTS
                )
                if not efforts:
                    continue
                suggested = entry.get("defaultReasoningEffort")
                default_effort = (
                    suggested if isinstance(suggested, str) and suggested in efforts else None
                )
                options.append(
                    ModelOption(
                        provider="codex-oauth",
                        model=model,
                        reasoning_efforts=efforts,
                        default_effort=default_effort,
                        capability_source="codex-app-server/model-list-pinned-v1",
                    )
                )
                if entry.get("isDefault") is True:
                    default = model
            next_cursor = result.get("nextCursor")
            if next_cursor is None:
                return tuple(options), default
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
                raise CodexProfileHold("CATALOG_UNAVAILABLE")
            cursor = next_cursor
        raise CodexProfileHold("CATALOG_UNAVAILABLE")

    def _refresh(self) -> CodexBrokerState:
        identity = self.profile.executable(version_runner=self._version_runner)
        if not self.profile.pin_path.is_file():
            return CodexBrokerState(False, False, "LOGIN_REQUIRED")
        self.profile.check_pin(identity)
        if self.profile.pending_login() and self._pending_login_id is None:
            return CodexBrokerState(False, False, "LOGIN_PENDING")
        if not self.profile.auth_path.is_file():
            return CodexBrokerState(False, False, "LOGIN_REQUIRED")
        with self.profile.lock():
            before = self.profile.read_auth(require_fresh=False)
            prior = None if self._cached is None else self._cached.snapshot
            if prior is not None and prior.account_id != before.account_id:
                raise CodexProfileHold("CODEX_AUTH_CACHE_CONVERGENCE_FAILED")
            needs_refresh = before.expires_at <= datetime.now(UTC) + timedelta(minutes=5)
            held_digest = self.profile.refresh_hold_digest()
            if held_digest is not None and (held_digest == before.content_digest or needs_refresh):
                raise CodexProfileHold("CODEX_REFRESH_OUTCOME_UNKNOWN")
            client = self._client_for(identity)
            started = datetime.now(UTC)
            try:
                account = client.call("account/read", {"refreshToken": needs_refresh})
            except CodexProfileHold:
                if needs_refresh:
                    self.profile.mark_refresh_hold(before.content_digest)
                raise
            detail = account.get("account")
            if (
                not isinstance(detail, dict)
                or cast(dict[str, object], detail).get("type") != "chatgpt"
            ):
                if needs_refresh:
                    self.profile.mark_refresh_hold(before.content_digest)
                return CodexBrokerState(False, False, "LOGIN_REQUIRED")
            try:
                candidate = self.profile.read_auth(require_fresh=False)
                if candidate.account_id != before.account_id or (
                    prior is not None and candidate.account_id != prior.account_id
                ):
                    raise CodexProfileHold("CODEX_AUTH_CACHE_CONVERGENCE_FAILED")
                self._routing(account, candidate)
                if needs_refresh:
                    if (
                        candidate.content_digest == before.content_digest
                        or candidate.last_refresh < started - timedelta(seconds=5)
                    ):
                        raise CodexProfileHold("CODEX_REFRESH_NOT_CONFIRMED")
                elif candidate.content_digest != before.content_digest:
                    raise CodexProfileHold("CODEX_AUTH_SNAPSHOT_CHANGED")
                snapshot = self.profile.read_auth()
            except CodexProfileHold:
                if needs_refresh:
                    self.profile.mark_refresh_hold(before.content_digest)
                raise
            if held_digest is not None:
                self.profile.clear_refresh_hold()
            options, default_id = self._models(client)
            if self._pending_login_id is not None:
                self.profile.clear_login_pending()
                self._pending_login_id = None
                if self._login_timer is not None:
                    self._login_timer.cancel()
                    self._login_timer = None
        if not options:
            return CodexBrokerState(True, False, "CATALOG_UNAVAILABLE", snapshot=snapshot)
        defaults = ModelSelection(provider="codex-oauth")
        if default_id is not None:
            option = next((item for item in options if item.model == default_id), None)
            if option is not None:
                defaults = ModelSelection(
                    provider="codex-oauth",
                    model=default_id,
                    reasoning_effort=option.default_effort,
                )
        return CodexBrokerState(True, True, "EXECUTION_UNVERIFIED", options, defaults, snapshot)

    def state(self, *, force: bool = False) -> CodexBrokerState:
        with self._lock:
            if not force and self._cached is not None and time.monotonic() - self._cached_at < 30:
                snapshot = self._cached.snapshot
                try:
                    identity = self.profile.executable(version_runner=self._version_runner)
                    self.profile.check_pin(identity)
                    if snapshot is None:
                        if (
                            self._cached.reason_code == "LOGIN_REQUIRED"
                            and self.profile.auth_path.is_file()
                        ) or self._cached.reason_code == "LOGIN_PENDING":
                            force = True
                        else:
                            return self._cached
                    elif (
                        snapshot.expires_at > datetime.now(UTC)
                        and self.profile.read_auth().content_digest == snapshot.content_digest
                    ):
                        return self._cached
                except CodexProfileHold:
                    force = True
            try:
                result = self._refresh()
            except CodexProfileHold as exc:
                result = CodexBrokerState(False, False, str(exc))
            self._cached, self._cached_at = result, time.monotonic()
            return result

    def cached_state(self) -> CodexBrokerState:
        """In-memory projection only; safe for callers inside an existing SQLite UoW."""
        return self._cached or CodexBrokerState(False, False, "CATALOG_UNAVAILABLE")

    def local_status(self) -> CodexBrokerState:
        """Inspect this THOTH profile without App Server refresh or model discovery."""
        with self._lock:
            snapshot: CodexAuthSnapshot | None = None
            if self.profile.auth_path.is_file():
                try:
                    snapshot = self.profile.read_auth(require_fresh=False)
                except CodexProfileHold as exc:
                    return CodexBrokerState(False, False, str(exc))
            if self.profile.pending_login() and snapshot is None:
                return CodexBrokerState(False, False, "LOGIN_PENDING")
            if snapshot is None:
                return CodexBrokerState(False, False, "LOGIN_REQUIRED")
            try:
                held_digest = self.profile.refresh_hold_digest()
            except CodexProfileHold as exc:
                return CodexBrokerState(False, False, str(exc))
            if held_digest == snapshot.content_digest:
                return CodexBrokerState(False, False, "CODEX_REFRESH_OUTCOME_UNKNOWN")
            cached = self._cached
            if (
                cached is not None
                and cached.snapshot is not None
                and (
                    cached.snapshot.content_digest == snapshot.content_digest
                    and cached.snapshot.account_id == snapshot.account_id
                    and snapshot.expires_at > datetime.now(UTC)
                )
            ):
                try:
                    identity = self.profile.executable(version_runner=self._version_runner)
                    self.profile.check_pin(identity)
                except CodexProfileHold as exc:
                    return CodexBrokerState(False, False, str(exc))
                return cached
            return CodexBrokerState(True, False, "CATALOG_UNAVAILABLE", snapshot=snapshot)

    def login_status(self, login_id: str | None = None) -> dict[str, object]:
        with self._lock:
            pending = self._pending_login_id or self.profile.pending_login_id()
            if login_id is not None and login_id != pending:
                raise CodexProfileHold("CODEX_LOGIN_NOT_FOUND")
            state = self.local_status()
            return {
                **state.public(),
                "login_id": pending,
                "login_state": "PENDING" if pending is not None else "IDLE",
                "auth_state": "CONNECTED" if state.connected else "DISCONNECTED",
                "catalog_state": "AVAILABLE" if state.execution_eligible else "UNAVAILABLE",
            }

    def cancel_login(self, login_id: str) -> dict[str, object]:
        with self._lock:
            pending = self._pending_login_id or self.profile.pending_login_id()
            if pending != login_id:
                raise CodexProfileHold("CODEX_LOGIN_NOT_FOUND")
            if self._client is not None and self._pending_login_id == login_id:
                try:
                    self._client.call("account/login/cancel", {"loginId": login_id})
                except CodexProfileHold:
                    pass
                finally:
                    self._client.close()
                    self._client = None
            if self._login_timer is not None:
                self._login_timer.cancel()
                self._login_timer = None
            with self.profile.lock():
                self.profile.clear_login_pending()
            self._pending_login_id = None
            state = self.local_status()
            return {
                **state.public(),
                "login_id": login_id,
                "login_state": "CANCELLED",
                "auth_state": "CONNECTED" if state.connected else "DISCONNECTED",
                "reason_code": "CODEX_LOGIN_CANCELLED",
            }

    def session_bound(self, model: str | None) -> tuple[OAuthSession, str]:
        state = self.state()
        if not state.execution_eligible or state.snapshot is None:
            raise CodexProfileHold(state.reason_code)
        selected = model or state.default.model
        if not isinstance(selected, str):
            raise CodexProfileHold("OAUTH_MODEL_SELECTION_REQUIRED")
        option = next((item for item in state.options if item.model == selected), None)
        if option is None:
            raise CodexProfileHold("OAUTH_MODEL_NOT_AVAILABLE")
        return (
            OAuthSession(
                state.snapshot.access_token,
                state.snapshot.account_id,
                selected,
                option.default_effort,
            ),
            state.snapshot.content_digest,
        )

    def session(self, model: str | None) -> OAuthSession:
        return self.session_bound(model)[0]

    def validate_selection(self, model: str, effort: str | None) -> None:
        state = self.state()
        option = next((item for item in state.options if item.model == model), None)
        if not state.execution_eligible or option is None:
            raise CodexProfileHold("OAUTH_MODEL_NOT_AVAILABLE")
        if effort is not None and effort not in option.reasoning_efforts:
            raise CodexProfileHold("OAUTH_REASONING_EFFORT_UNSUPPORTED")

    def _check_dispatch_session(
        self, session: OAuthSession, expected_digest: str | None = None
    ) -> None:
        identity = self.profile.executable(version_runner=self._version_runner)
        self.profile.check_pin(identity)
        current = self.profile.read_auth(require_fresh=False)
        cached = self._cached
        if (
            cached is None
            or cached.snapshot is None
            or cached.snapshot.content_digest != current.content_digest
            or (expected_digest is not None and expected_digest != current.content_digest)
            or current.account_id != session.account_id
            or current.expires_at <= datetime.now(UTC) + timedelta(seconds=5)
            or not hmac.compare_digest(current.access_token, session.access_token)
        ):
            raise CodexProfileHold("OAUTH_AUTH_SNAPSHOT_CHANGED")

    @contextmanager
    def dispatch_gate(self, session: OAuthSession) -> Generator[None]:
        """Synchronous compatibility path for a non-async transport owner."""
        with self._lock, self.profile.lock(timeout_seconds=8):
            self._check_dispatch_session(session)
            yield

    @asynccontextmanager
    async def dispatch_gate_async(
        self, session: OAuthSession, *, expected_digest: str | None = None
    ) -> AsyncGenerator[None]:
        """Fence identity through first send without blocking the event loop on file locks."""
        async with self.profile.async_lock(timeout_seconds=8):
            await asyncio.to_thread(self._check_dispatch_session, session, expected_digest)
            yield

    def _expire_login(self, login_id: str) -> None:
        with self._lock:
            if self._pending_login_id != login_id:
                return
            if self._client is not None:
                self._client.close()
                self._client = None
            with self.profile.lock():
                self.profile.clear_login_pending()
            self._pending_login_id = None
            self._cached = None

    def start_login(
        self, *, wait_for_completion: bool = False, timeout_seconds: float = 180
    ) -> dict[str, object]:
        with self._lock:
            identity = self.profile.executable(version_runner=self._version_runner)
            self.profile.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            with self.profile.lock():
                if self.profile.pending_login():
                    raise CodexProfileHold("CODEX_LOGIN_ALREADY_PENDING")
                self.profile.prepare_login(identity)
                result = self._client_for(identity).call(
                    "account/login/start",
                    {"type": "chatgpt", "useHostedLoginSuccessPage": True, "appBrand": "codex"},
                )
                url, login_id = result.get("authUrl"), result.get("loginId")
                if (
                    result.get("type") != "chatgpt"
                    or not isinstance(url, str)
                    or not url.startswith("https://")
                    or not isinstance(login_id, str)
                    or not login_id
                ):
                    raise CodexProfileHold("CODEX_LOGIN_START_FAILED")
                self.profile.mark_login_pending(login_id)
                self._pending_login_id = login_id
            self._cached = None
            if not self._browser_opener(url):
                self._expire_login(login_id)
                raise CodexProfileHold("CODEX_LOGIN_BROWSER_UNAVAILABLE")
            response: dict[str, object] = {
                "provider": "codex-oauth",
                "login_id": login_id,
                "login_state": "PENDING",
                "kind": "oauth",
                "started": True,
                "connected": False,
                "connection_state": "LOGIN_PENDING",
                "profile_mode": "THOTH_ISOLATED",
                "reason_code": "LOGIN_STARTED_NOT_CONNECTED",
                "execution_eligible": False,
                "execution_verified": False,
                "guidance": "Finish the THOTH Codex sign-in, then check connection status",
            }
            if wait_for_completion:
                try:
                    completed = self._client_for(identity).wait_for_login(
                        login_id, timeout_seconds=timeout_seconds
                    )
                    if not completed:
                        response["reason_code"] = "CODEX_LOGIN_FAILED"
                        response["connection_state"] = "LOGIN_REQUIRED"
                    else:
                        state = self.state(force=True)
                        response.update(state.public())
                        response["guidance"] = (
                            "The Codex route is eligible; live execution has not been verified"
                            if state.execution_eligible
                            else "Check the isolated Codex connection and model catalog"
                        )
                except CodexProfileHold as exc:
                    response["reason_code"] = str(exc)
                    response["connection_state"] = "LOGIN_REQUIRED"
                finally:
                    self.close()
                return response
            timer = threading.Timer(300, self._expire_login, args=(login_id,))
            timer.daemon = True
            self._login_timer = timer
            timer.start()
            return response

    def close(self) -> None:
        with self._lock:
            if self._login_timer is not None:
                self._login_timer.cancel()
                self._login_timer = None
            if self._pending_login_id is not None and self.profile.root.is_dir():
                with self.profile.lock():
                    self.profile.clear_login_pending()
                self._pending_login_id = None
            if self._client is not None:
                self._client.close()
                self._client = None


_BROKERS: dict[Path, CodexAuthBroker] = {}
_BROKER_USERS: dict[Path, int] = {}
_BROKERS_LOCK = threading.RLock()


def broker_for_workspace(workspace: Path) -> CodexAuthBroker:
    resolved = workspace.resolve()
    with _BROKERS_LOCK:
        if resolved not in _BROKERS:
            _BROKERS[resolved] = CodexAuthBroker(resolved)
        return _BROKERS[resolved]


def close_workspace_broker(workspace: Path) -> None:
    with _BROKERS_LOCK:
        key = workspace.resolve()
        if _BROKER_USERS.get(key, 0) > 0:
            raise CodexProfileHold("CODEX_BROKER_RUNTIME_OWNED")
        broker = _BROKERS.pop(key, None)
    if broker is not None:
        broker.close()


def retain_workspace_broker(workspace: Path) -> CodexAuthBroker:
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
            raise CodexProfileHold("CODEX_BROKER_OWNER_MISSING")
        if users > 1:
            _BROKER_USERS[key] = users - 1
            return
        _BROKER_USERS.pop(key, None)
        broker = _BROKERS.pop(key, None)
    if broker is not None:
        broker.close()


def close_all_brokers() -> None:
    with _BROKERS_LOCK:
        brokers = tuple(_BROKERS.values())
        _BROKERS.clear()
        _BROKER_USERS.clear()
    for broker in brokers:
        broker.close()


atexit.register(close_all_brokers)


__all__ = [
    "CodexAuthBroker",
    "CodexBrokerState",
    "broker_for_workspace",
    "close_workspace_broker",
    "release_workspace_broker",
    "retain_workspace_broker",
]
