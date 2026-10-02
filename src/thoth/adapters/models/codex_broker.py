"""Official App Server auth/catalog owner for a THOTH-only Codex profile."""

from __future__ import annotations

import asyncio
import atexit
import hmac
import threading
import time
import webbrowser
from collections.abc import AsyncGenerator, Callable, Generator
from contextlib import asynccontextmanager, contextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol, cast

from thoth.adapters.models.catalog import WIRE_EFFORTS, classify_codex_model
from thoth.adapters.models.catalog_store import CatalogSnapshotStore, CatalogStoreError
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
from thoth.domain.model_catalog import (
    CatalogSnapshot,
    ExcludedModel,
    ExecutionMark,
    authority_digest,
)
from thoth.domain.model_dispatch import OAuthSession
from thoth.domain.model_settings import ModelOption, ModelSelection


class AppServerPort(Protocol):
    def call(self, method: str, params: dict[str, object]) -> dict[str, object]: ...
    def wait_for_login(self, login_id: str, *, timeout_seconds: float) -> bool: ...
    def close(self) -> None: ...


ClientFactory = Callable[[CodexProfile, CodexExecutableIdentity], AppServerPort]
BrowserOpener = Callable[[str], bool]


# One list load per finished login is given this long before the login is shown without a list.
LOGIN_LIST_DEADLINE_SECONDS = 20.0


@dataclass(frozen=True)
class CodexBrokerState:
    connected: bool
    execution_eligible: bool
    reason_code: str
    options: tuple[ModelOption, ...] = ()
    default: ModelSelection = field(default_factory=lambda: ModelSelection(provider="codex-oauth"))
    snapshot: CodexAuthSnapshot | None = None
    catalog: CatalogSnapshot | None = None

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
        login_watch_interval: float = 2.0,
    ) -> None:
        self.profile = CodexProfile.for_workspace(workspace)
        self._client_factory = client_factory or CodexAppServerClient
        self._version_runner = version_runner
        self._browser_opener = browser_opener
        self._client: AppServerPort | None = None
        self._lock = threading.RLock()
        self._cached: CodexBrokerState | None = None
        self._store = CatalogSnapshotStore(self.profile.workspace)
        self._catalog: CatalogSnapshot | None = None
        self._cached_at = 0.0
        self._pending_login_id: str | None = None
        self._login_timer: threading.Timer | None = None
        # Finishing a login is the server's job: it settles the attempt and loads the model list
        # once, whether or not a screen is still polling. Bookkeeping has its own lock so status
        # reads stay quick while the list loads.
        self._login_watch_interval = login_watch_interval
        self._login_lock = threading.Lock()
        self._login_runs: dict[str, str] = {}
        self._login_starts: dict[str, str | None] = {}
        self._login_stop = threading.Event()

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
    def _models(
        client: AppServerPort,
    ) -> tuple[tuple[ModelOption, ...], str | None, tuple[ExcludedModel, ...]]:
        options: list[ModelOption] = []
        excluded: list[ExcludedModel] = []
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
                model, reason = classify_codex_model(entry.get("model"))
                if entry.get("hidden") is True or (model is None and reason is None):
                    continue
                if model is None:
                    if reason is not None:
                        excluded.append(
                            ExcludedModel(model=str(entry.get("model"))[:160], reason=reason)
                        )
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
                    excluded.append(ExcludedModel(model=model, reason="UNKNOWN_EFFORT_ONLY"))
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
                        entitlement="PROVIDER_LISTED",
                    )
                )
                if entry.get("isDefault") is True:
                    default = model
            next_cursor = result.get("nextCursor")
            if next_cursor is None:
                return tuple(options), default, tuple(excluded)
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
                raise CodexProfileHold("CATALOG_UNAVAILABLE")
            cursor = next_cursor
        raise CodexProfileHold("CATALOG_UNAVAILABLE")

    def _refresh(self, *, refresh_catalog: bool = True) -> CodexBrokerState:
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
            catalog = self._catalog_for_refresh(client, snapshot, refresh_catalog)
            if self._pending_login_id is not None:
                self.profile.clear_login_pending()
                self._pending_login_id = None
                if self._login_timer is not None:
                    self._login_timer.cancel()
                    self._login_timer = None
        if catalog is None or not catalog.options:
            return CodexBrokerState(True, False, "CATALOG_UNAVAILABLE", snapshot=snapshot)
        return self._eligible(catalog, snapshot)

    @staticmethod
    def _eligible(catalog: CatalogSnapshot, snapshot: CodexAuthSnapshot) -> CodexBrokerState:
        options = catalog.effective_options()
        defaults = ModelSelection(provider="codex-oauth")
        option = next((item for item in options if item.model == catalog.default_model), None)
        if option is not None:
            defaults = ModelSelection(
                provider="codex-oauth", model=option.model, reasoning_effort=option.default_effort
            )
        return CodexBrokerState(
            True, True, "EXECUTION_UNVERIFIED", options, defaults, snapshot, catalog
        )

    @staticmethod
    def _authority(snapshot: CodexAuthSnapshot) -> str:
        return authority_digest("codex-oauth", snapshot.account_id)

    def _stored_catalog(self, digest: str) -> CatalogSnapshot | None:
        if self._catalog is not None and self._catalog.authority_digest == digest:
            return self._catalog
        self._catalog = self._store.load("codex-oauth", digest)
        return self._catalog

    def _publish(self, catalog: CatalogSnapshot) -> CatalogSnapshot:
        self._catalog = catalog
        # If the write fails the in-memory list still serves; the next good refresh tries again.
        with suppress(CatalogStoreError):
            self._store.save(catalog)
        return catalog

    def _catalog_for_refresh(
        self, client: AppServerPort, snapshot: CodexAuthSnapshot, refresh: bool
    ) -> CatalogSnapshot | None:
        """The list to use now. A failed or bad candidate never replaces a good list."""

        digest = self._authority(snapshot)
        prior = self._stored_catalog(digest)
        if prior is not None and not refresh:
            return prior
        now = datetime.now(UTC)
        try:
            options, default_id, excluded = self._models(client)
        except CodexProfileHold as exc:
            if prior is None:
                return None
            return self._publish(
                prior.model_copy(
                    update={
                        "status": "STALE_LAST_GOOD",
                        "failure_reason": str(exc)[:160],
                        "last_attempt_at": now,
                    }
                )
            )
        listed = {option.model for option in options}
        kept = tuple(
            mark
            for mark in (() if prior is None else prior.executions)
            if mark.state == "VERIFIED" and mark.model in listed
        )
        return self._publish(
            CatalogSnapshot(
                provider="codex-oauth",
                source="PROVIDER_LIST",
                authority_digest=digest,
                fetched_at=now,
                validated_at=now,
                options=options,
                excluded=excluded,
                default_model=default_id,
                last_attempt_at=now,
                executions=kept,
            )
        )

    def catalog_snapshot(self) -> CatalogSnapshot | None:
        """The stored list of the signed-in account."""

        try:
            auth = self.profile.read_auth(require_fresh=False)
        except CodexProfileHold:
            return None
        return self._stored_catalog(self._authority(auth))

    def record_execution(self, model: str, state: str, reason_code: str | None) -> None:
        """Remember what a real request showed about one listed model; nothing is switched."""

        with self._lock:
            catalog = self.catalog_snapshot()
            if catalog is None or state not in {"VERIFIED", "REJECTED"}:
                return
            marks = tuple(mark for mark in catalog.executions if mark.model != model)
            if model in {option.model for option in catalog.options}:
                marks = (
                    *marks,
                    ExecutionMark(
                        model=model,
                        state="VERIFIED" if state == "VERIFIED" else "REJECTED",
                        reason_code=reason_code,
                        observed_at=datetime.now(UTC),
                    ),
                )
            updated = self._publish(catalog.model_copy(update={"executions": marks}))
            cached = self._cached
            if cached is not None and cached.snapshot is not None:
                self._cached = self._eligible(updated, cached.snapshot)

    def state(self, *, force: bool = False) -> CodexBrokerState:
        refresh_catalog = force
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
                result = self._refresh(refresh_catalog=refresh_catalog)
            except CodexProfileHold as exc:
                result = CodexBrokerState(False, False, str(exc))
            self._cached, self._cached_at = result, time.monotonic()
            return result

    def cached_state(self) -> CodexBrokerState:
        """In-memory projection only; safe for callers inside an existing SQLite UoW."""
        if self._cached is not None:
            return self._cached
        return self._saved_state() or CodexBrokerState(False, False, "CATALOG_UNAVAILABLE")

    def _saved_state(self) -> CodexBrokerState | None:
        """The stored list of the signed-in account; no provider call, no version probe."""

        if not self.profile.auth_path.is_file():
            return None
        try:
            snapshot = self.profile.read_auth(require_fresh=False)
        except CodexProfileHold:
            return None
        catalog = self._stored_catalog(self._authority(snapshot))
        if catalog is None or not catalog.options:
            return None
        return self._eligible(catalog, snapshot)

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
            saved = self._saved_state()
            if saved is not None:
                try:
                    identity = self.profile.executable(version_runner=self._version_runner)
                    self.profile.check_pin(identity)
                except CodexProfileHold as exc:
                    return CodexBrokerState(False, False, str(exc))
                return saved
            return CodexBrokerState(True, False, "CATALOG_UNAVAILABLE", snapshot=snapshot)

    def _auth_digest(self) -> str | None:
        try:
            return self.profile.read_auth(require_fresh=False).content_digest
        except (CodexProfileHold, OSError):
            return None

    def _login_view(
        self, login_id: str | None, state: CodexBrokerState, login_state: str, refresh: str | None
    ) -> dict[str, object]:
        view: dict[str, object] = {
            **state.public(),
            "login_id": login_id,
            "login_state": login_state,
            "auth_state": "CONNECTED" if state.connected else "DISCONNECTED",
            "catalog_state": "AVAILABLE" if state.execution_eligible else "UNAVAILABLE",
        }
        if refresh is not None:
            view["catalog_refresh"] = refresh
        return view

    def login_status(self, login_id: str | None = None) -> dict[str, object]:
        if login_id is not None:
            with self._login_lock:
                run = self._login_runs.get(login_id)
            if run == "REFRESHING":
                # The sign-in is done and the list is loading; answering needs no provider call.
                return self._login_view(
                    login_id,
                    CodexBrokerState(True, False, "CATALOG_UNAVAILABLE"),
                    "PENDING",
                    "RUNNING",
                )
            if run is not None:
                return self._login_view(login_id, self.local_status(), "CONNECTED", run)
        with self._lock:
            pending = self._pending_login_id or self.profile.pending_login_id()
            if login_id is not None and login_id != pending:
                raise CodexProfileHold("CODEX_LOGIN_NOT_FOUND")
            if (
                login_id is not None
                and login_id in self._login_starts
                and self._auth_digest() not in (None, self._login_starts[login_id])
            ):
                threading.Thread(target=self._finish_login, args=(login_id,), daemon=True).start()
                return self._login_view(
                    login_id,
                    CodexBrokerState(True, False, "CATALOG_UNAVAILABLE"),
                    "PENDING",
                    "RUNNING",
                )
            state = self.local_status()
            return self._login_view(
                pending, state, "PENDING" if pending is not None else "IDLE", None
            )

    def _watch_login(self, login_id: str, stop: threading.Event) -> None:
        """Wait for the sign-in to produce new credentials, then finish the login once."""

        while not stop.wait(self._login_watch_interval):
            if self._pending_login_id != login_id:
                return
            if self._auth_digest() not in (None, self._login_starts.get(login_id)):
                self._finish_login(login_id)
                return

    def _finish_login(self, login_id: str) -> None:
        with self._login_lock:
            if login_id in self._login_runs:
                return
            self._login_runs[login_id] = "REFRESHING"
        outcome = "FAILED"
        try:
            loaded: list[CodexBrokerState] = []
            worker = threading.Thread(
                target=lambda: loaded.append(self.state(force=True)), daemon=True
            )
            worker.start()
            worker.join(LOGIN_LIST_DEADLINE_SECONDS)
            if loaded and loaded[0].execution_eligible:
                outcome = "DONE"
        except Exception:  # a failed list never undoes a finished login
            outcome = "FAILED"
        finally:
            with self._lock:
                if self._login_timer is not None:
                    self._login_timer.cancel()
                    self._login_timer = None
                if self.profile.root.is_dir():
                    with self.profile.lock():
                        self.profile.clear_login_pending()
                if self._pending_login_id == login_id:
                    self._pending_login_id = None
            with self._login_lock:
                self._login_runs[login_id] = outcome

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
            self._login_stop.set()
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
            self._login_stop.set()
            self._cached = None

    def start_login(
        self, *, wait_for_completion: bool = False, timeout_seconds: float = 180
    ) -> dict[str, object]:
        with self._lock:
            identity = self.profile.executable(version_runner=self._version_runner)
            self.profile.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            before_login = self._auth_digest()
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
                self._login_starts[login_id] = before_login
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
            self._login_stop = threading.Event()
            threading.Thread(
                target=self._watch_login, args=(login_id, self._login_stop), daemon=True
            ).start()
            return response

    def close(self) -> None:
        with self._lock:
            self._login_stop.set()
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
