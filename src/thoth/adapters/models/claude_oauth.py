"""THOTH-owned Claude PKCE attempt lifecycle; no external authentication read."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import secrets
import threading
import time
import uuid
from collections.abc import AsyncGenerator, Callable, Generator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from thoth.adapters.models.claude_profile import ClaudeCredential, ClaudeProfile, ClaudeProfileHold
from thoth.ports.model import ModelExecutionHold

AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
PREFERRED_CALLBACK_PORT = 53692
LOGIN_LIFETIME_SECONDS = 600
_REFRESH_SKEW_SECONDS = 300


class ClaudeOAuthHold(ModelExecutionHold):
    pass


@dataclass(frozen=True, repr=False)
class ClaudeSession:
    access_token: str = field(repr=False)
    profile_id: str
    generation: str
    expires_at: float


@dataclass(repr=False)
class _Attempt:
    login_id: str
    verifier: str = field(repr=False)
    state: str = field(repr=False)
    challenge: str
    redirect_uri: str
    expires_at: float
    manual_only: bool
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    login_state: str = "PENDING"
    reason_code: str = "LOGIN_PENDING"
    completion_started: bool = False
    listener: ThreadingHTTPServer | None = field(default=None, repr=False)
    listener_thread: threading.Thread | None = field(default=None, repr=False)
    worker: threading.Thread | None = field(default=None, repr=False)
    timer: threading.Timer | None = field(default=None, repr=False)
    cleanup_confirmed: bool = False
    listener_closed: bool = False


class ClaudeOAuthBroker:
    """One workspace's pending login and execution credential owner.

    A THOTH-registered client_id must be supplied explicitly. Another application's
    client ID is never used as a default or as a Claude Code identity.
    """

    def __init__(
        self,
        workspace: Path,
        *,
        client_id: str | None = None,
        scope: str = "user:inference",
        transport: httpx.BaseTransport | None = None,
        browser_opener: Callable[[str], object] | None = None,
        preferred_port: int = PREFERRED_CALLBACK_PORT,
        lifetime_seconds: float = LOGIN_LIFETIME_SECONDS,
    ) -> None:
        self.profile = ClaudeProfile(workspace)
        self.client_id = client_id
        self.scope = scope
        self.transport = transport
        self.browser_opener = browser_opener
        self.preferred_port = preferred_port
        self.lifetime_seconds = lifetime_seconds
        self._lock = threading.RLock()
        self._attempt: _Attempt | None = None

    def _connection(self) -> tuple[str, bool, str]:
        try:
            saved = self.profile.read()
        except ClaudeProfileHold as exc:
            return "UNKNOWN", False, str(exc)
        if saved is None:
            return "DISCONNECTED", False, "CLAUDE_LOGIN_REQUIRED"
        if saved.state == "REAUTH_REQUIRED":
            return "REAUTH_REQUIRED", False, "CLAUDE_REAUTH_REQUIRED"
        if saved.state != "READY":
            return "UNKNOWN", False, saved.state
        if saved.expires_at <= time.time() + _REFRESH_SKEW_SECONDS:
            return "REFRESH_REQUIRED", True, "CLAUDE_REFRESH_REQUIRED"
        return "CONNECTED", True, "EXECUTION_UNVERIFIED"

    def _public(self, attempt: _Attempt | None, *, started: bool = False) -> dict[str, object]:
        auth_state, connected, reason = self._connection()
        eligible = bool(self.client_id and connected)
        return {
            "provider": "anthropic",
            "account_provider": "anthropic",
            "auth_method": "claude_pkce",
            "route": "claude-oauth",
            "kind": "oauth",
            "profile_id": self.profile.profile_id,
            "login_id": None if attempt is None else attempt.login_id,
            "login_state": "IDLE" if attempt is None else attempt.login_state,
            "auth_state": auth_state,
            "catalog_state": "AVAILABLE" if eligible else "UNAVAILABLE",
            "connected": connected,
            "started": started,
            "execution_eligible": eligible,
            "execution_verified": False,
            "reason_code": (
                "CLAUDE_CLIENT_REGISTRATION_REQUIRED"
                if not self.client_id
                else reason
                if attempt is None or attempt.login_state == "CONNECTED"
                else attempt.reason_code
            ),
            "expires_at": None if attempt is None else int(attempt.expires_at),
            "manual_response_required": False if attempt is None else attempt.manual_only,
            "cleanup_confirmed": (
                True
                if attempt is None
                else attempt.login_state != "PENDING"
                and attempt.listener_closed
                and (attempt.worker is None or not attempt.worker.is_alive())
            ),
            "capabilities": {
                "start": bool(self.client_id),
                "status": True,
                "cancel": True,
                "manual_complete": bool(self.client_id),
            },
        }

    def status(self) -> dict[str, object]:
        """Local-only read: no refresh, token endpoint, browser or model call."""
        with self._lock:
            self._expire_if_needed()
            return self._public(self._attempt)

    def login_status(self, login_id: str | None = None) -> dict[str, object]:
        with self._lock:
            self._expire_if_needed()
            attempt = self._attempt
            if login_id is not None and (attempt is None or attempt.login_id != login_id):
                raise ClaudeOAuthHold("CLAUDE_LOGIN_NOT_FOUND")
            return self._public(attempt)

    def _listen(self, attempt: _Attempt, port: int) -> ThreadingHTTPServer:
        broker = self

        class CallbackHandler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                del format, args
                pass

            def do_GET(self) -> None:
                request = urlsplit(self.path)
                host = self.headers.get("Host", "").lower()
                expected_host = f"localhost:{cast(ThreadingHTTPServer, self.server).server_port}"
                if request.path != "/callback" or host != expected_host:
                    self.send_error(404)
                    return
                fields = parse_qs(request.query, keep_blank_values=True)
                states = fields.get("state", [])
                if len(states) != 1 or not hmac.compare_digest(states[0], attempt.state):
                    self.send_error(400)
                    return
                if "error" in fields:
                    broker._deny_attempt(attempt)
                    self.send_error(400)
                    return
                codes = fields.get("code", [])
                if len(codes) != 1:
                    self.send_error(400)
                    return
                try:
                    broker._accept(attempt, codes[0], states[0])
                except ClaudeOAuthHold:
                    self.send_error(400)
                    return
                self.send_response(202)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"Authorization received. Return to THOTH for status.")

        return ThreadingHTTPServer(("127.0.0.1", port), CallbackHandler)

    def _bind_listener(self, attempt: _Attempt) -> None:
        for port in (self.preferred_port, 0):
            try:
                server = self._listen(attempt, port)
            except OSError as exc:
                if getattr(exc, "errno", None) not in {13, 98, 10013, 10048}:
                    raise ClaudeOAuthHold("CLAUDE_CALLBACK_BIND_FAILED") from exc
                continue
            attempt.listener = server
            attempt.redirect_uri = f"http://localhost:{server.server_port}/callback"
            thread = threading.Thread(
                target=server.serve_forever,
                kwargs={"poll_interval": 0.05},
                daemon=True,
                name="thoth-claude-callback",
            )
            attempt.listener_thread = thread
            thread.start()
            return
        attempt.manual_only = True
        attempt.redirect_uri = f"http://localhost:{self.preferred_port}/callback"
        attempt.listener_closed = True

    def _stop_listener(self, attempt: _Attempt) -> None:
        server = attempt.listener
        if server is None:
            attempt.listener_closed = True
            return

        def close() -> None:
            server.shutdown()
            server.server_close()
            thread = attempt.listener_thread
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=2)
            attempt.listener_closed = thread is None or not thread.is_alive()
            attempt.cleanup_confirmed = attempt.listener_closed and (
                attempt.worker is None or not attempt.worker.is_alive()
            )

        threading.Thread(target=close, daemon=True, name="thoth-claude-callback-close").start()

    def _expire_if_needed(self) -> None:
        attempt = self._attempt
        if (
            attempt is not None
            and attempt.login_state == "PENDING"
            and time.time() >= attempt.expires_at
        ):
            attempt.cancel.set()
            attempt.login_state, attempt.reason_code = "EXPIRED", "CLAUDE_LOGIN_EXPIRED"
            self._stop_listener(attempt)

    def _expire(self, attempt: _Attempt) -> None:
        with self._lock:
            if self._attempt is attempt:
                self._expire_if_needed()

    def start_login(self) -> dict[str, object]:
        if not self.client_id or not self.client_id.strip():
            raise ClaudeOAuthHold("CLAUDE_CLIENT_REGISTRATION_REQUIRED")
        verifier = secrets.token_urlsafe(32)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        attempt = _Attempt(
            uuid.uuid4().hex,
            verifier,
            secrets.token_urlsafe(32),
            challenge,
            "",
            time.time() + self.lifetime_seconds,
            False,
        )
        with self._lock:
            previous = self._attempt
            if previous is not None and previous.login_state == "PENDING":
                previous.cancel.set()
                previous.login_state = "CANCELLED"
                previous.reason_code = "CLAUDE_LOGIN_SUPERSEDED"
                if previous.timer is not None:
                    previous.timer.cancel()
                self._stop_listener(previous)
            self._bind_listener(attempt)
            self._attempt = attempt
            timer = threading.Timer(self.lifetime_seconds, self._expire, args=(attempt,))
            timer.daemon = True
            attempt.timer = timer
            timer.start()
            query = urlencode(
                {
                    "code": "true",
                    "client_id": self.client_id,
                    "response_type": "code",
                    "redirect_uri": attempt.redirect_uri,
                    "scope": self.scope,
                    "code_challenge": attempt.challenge,
                    "code_challenge_method": "S256",
                    "state": attempt.state,
                }
            )
            url = f"{AUTHORIZE_URL}?{query}"
            result = self._public(attempt, started=True)
            # This URL is transient UI material. The common RPC owner must never
            # persist its state/challenge-bearing query in an operation result.
            result["authorization_url"] = url
        if self.browser_opener is not None:
            try:
                self.browser_opener(url)
            except Exception as exc:
                self.cancel_login(attempt.login_id)
                raise ClaudeOAuthHold("CLAUDE_BROWSER_START_FAILED") from exc
        return result

    def _deny_attempt(self, attempt: _Attempt) -> None:
        with self._lock:
            if self._attempt is not attempt or attempt.login_state != "PENDING":
                return
            attempt.cancel.set()
            attempt.login_state, attempt.reason_code = "DENIED", "CLAUDE_LOGIN_DENIED"
            if attempt.timer is not None:
                attempt.timer.cancel()
            self._stop_listener(attempt)

    def _manual_code(self, attempt: _Attempt, response: str) -> tuple[str, str]:
        value = response.strip()
        if not value or len(value) > 4096:
            raise ClaudeOAuthHold("CLAUDE_LOGIN_RESPONSE_INVALID")
        if value.startswith(("http://", "https://")):
            parsed = urlsplit(value)
            expected = urlsplit(attempt.redirect_uri)
            if (
                (parsed.scheme, parsed.hostname, parsed.port, parsed.path)
                != (
                    expected.scheme,
                    expected.hostname,
                    expected.port,
                    expected.path,
                )
                or parsed.username is not None
                or parsed.password is not None
                or parsed.fragment
            ):
                raise ClaudeOAuthHold("CLAUDE_LOGIN_RESPONSE_INVALID")
            params = parse_qs(parsed.query, keep_blank_values=True)
            if len(params.get("code", [])) != 1 or len(params.get("state", [])) != 1:
                raise ClaudeOAuthHold("CLAUDE_LOGIN_RESPONSE_INVALID")
            code, state = params["code"][0], params["state"][0]
        elif "#" in value:
            code, state = value.split("#", 1)
        elif value.startswith("code="):
            params = parse_qs(value, keep_blank_values=True)
            if len(params.get("code", [])) != 1 or len(params.get("state", [])) != 1:
                raise ClaudeOAuthHold("CLAUDE_LOGIN_RESPONSE_INVALID")
            code, state = params["code"][0], params["state"][0]
        else:
            code, state = value, attempt.state
        if not code or len(code) > 1024 or not hmac.compare_digest(state, attempt.state):
            raise ClaudeOAuthHold("CLAUDE_LOGIN_RESPONSE_INVALID")
        return code, state

    def submit_login_response(self, login_id: str, response: str) -> dict[str, object]:
        with self._lock:
            attempt = self._attempt
            if attempt is None or attempt.login_id != login_id:
                raise ClaudeOAuthHold("CLAUDE_LOGIN_NOT_FOUND")
            code, state = self._manual_code(attempt, response)
            self._accept(attempt, code, state)
            return self._public(attempt)

    def _accept(self, attempt: _Attempt, code: str, state: str) -> None:
        with self._lock:
            self._expire_if_needed()
            if (
                self._attempt is not attempt
                or attempt.login_state != "PENDING"
                or attempt.cancel.is_set()
                or attempt.completion_started
                or not hmac.compare_digest(state, attempt.state)
            ):
                raise ClaudeOAuthHold("CLAUDE_LOGIN_NOT_PENDING")
            attempt.completion_started = True
            attempt.reason_code = "CLAUDE_TOKEN_EXCHANGE_PENDING"
            worker = threading.Thread(
                target=self._exchange_worker,
                args=(attempt, code),
                daemon=True,
                name="thoth-claude-token-exchange",
            )
            attempt.worker = worker
            worker.start()

    def _post_token(self, payload: dict[str, str]) -> tuple[int, dict[str, object]]:
        try:
            with httpx.Client(
                transport=self.transport, timeout=httpx.Timeout(5), follow_redirects=False
            ) as client:
                response = client.post(
                    TOKEN_URL, json=payload, headers={"Accept": "application/json"}
                )
                raw: object = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ClaudeOAuthHold("CLAUDE_TOKEN_OUTCOME_UNKNOWN") from exc
        if not isinstance(raw, dict):
            raise ClaudeOAuthHold("CLAUDE_TOKEN_OUTCOME_UNKNOWN")
        return response.status_code, cast(dict[str, object], raw)

    @staticmethod
    def _token(
        data: dict[str, object], *, previous_refresh: str | None = None
    ) -> tuple[str, str, float]:
        access = data.get("access_token")
        refresh = data.get("refresh_token", previous_refresh)
        expires = data.get("expires_in")
        if (
            not isinstance(access, str)
            or not access
            or not isinstance(refresh, str)
            or not refresh
            or not isinstance(expires, (int, float))
            or isinstance(expires, bool)
            or not 0 < expires <= 86400
        ):
            raise ClaudeOAuthHold("CLAUDE_TOKEN_SCHEMA_UNSUPPORTED")
        return access, refresh, time.time() + float(expires)

    def _exchange_worker(self, attempt: _Attempt, code: str) -> None:
        try:
            status, data = self._post_token(
                {
                    "grant_type": "authorization_code",
                    "client_id": self.client_id or "",
                    "code": code,
                    "state": attempt.state,
                    "redirect_uri": attempt.redirect_uri,
                    "code_verifier": attempt.verifier,
                }
            )
            if status != 200:
                reason = (
                    "CLAUDE_CLIENT_UNSUPPORTED"
                    if data.get("error") in {"invalid_client", "unauthorized_client"}
                    else "CLAUDE_TOKEN_REJECTED"
                )
                raise ClaudeOAuthHold(reason)
            access, refresh, expires_at = self._token(data)
            with self._lock, self.profile.lock():
                if (
                    self._attempt is not attempt
                    or attempt.cancel.is_set()
                    or attempt.login_state != "PENDING"
                    or time.time() >= attempt.expires_at
                ):
                    return
                self.profile.save(
                    ClaudeCredential(
                        access, refresh, expires_at, uuid.uuid4().hex, self.profile.profile_id
                    )
                )
                attempt.login_state, attempt.reason_code = "CONNECTED", "EXECUTION_UNVERIFIED"
                if attempt.timer is not None:
                    attempt.timer.cancel()
                self._stop_listener(attempt)
        except (ClaudeOAuthHold, ClaudeProfileHold) as exc:
            with self._lock:
                if self._attempt is attempt and not attempt.cancel.is_set():
                    attempt.login_state, attempt.reason_code = "FAILED", str(exc)
                    self._stop_listener(attempt)
        except Exception:
            with self._lock:
                if self._attempt is attempt and not attempt.cancel.is_set():
                    attempt.login_state, attempt.reason_code = (
                        "FAILED",
                        "CLAUDE_TOKEN_EXCHANGE_FAILED",
                    )
                    self._stop_listener(attempt)
        finally:
            with self._lock:
                if attempt.listener is None or (
                    attempt.listener_thread is not None and not attempt.listener_thread.is_alive()
                ):
                    attempt.cleanup_confirmed = True

    def cancel_login(self, login_id: str) -> dict[str, object]:
        with self._lock:
            attempt = self._attempt
            if attempt is None or attempt.login_id != login_id:
                raise ClaudeOAuthHold("CLAUDE_LOGIN_NOT_FOUND")
            if attempt.login_state != "PENDING":
                return self._public(attempt)
            attempt.cancel.set()
            attempt.login_state, attempt.reason_code = "CANCELLED", "CLAUDE_LOGIN_CANCELLED"
            if attempt.timer is not None:
                attempt.timer.cancel()
            self._stop_listener(attempt)
            return self._public(attempt)

    def execution_session(self) -> ClaudeSession:
        if not self.client_id:
            raise ClaudeOAuthHold("CLAUDE_CLIENT_REGISTRATION_REQUIRED")
        with self.profile.lock():
            saved = self.profile.read()
            if saved is None:
                raise ClaudeOAuthHold("CLAUDE_LOGIN_REQUIRED")
            if saved.state != "READY":
                raise ClaudeOAuthHold(saved.state)
            if saved.expires_at > time.time() + _REFRESH_SKEW_SECONDS:
                return ClaudeSession(
                    saved.access_token, saved.profile_id, saved.generation, saved.expires_at
                )
            try:
                status, data = self._post_token(
                    {
                        "grant_type": "refresh_token",
                        "client_id": self.client_id,
                        "refresh_token": saved.refresh_token,
                    }
                )
            except ClaudeOAuthHold as exc:
                self.profile.save(
                    ClaudeCredential(
                        saved.access_token,
                        saved.refresh_token,
                        saved.expires_at,
                        saved.generation,
                        saved.profile_id,
                        "CLAUDE_REFRESH_OUTCOME_UNKNOWN",
                    )
                )
                raise ClaudeOAuthHold("CLAUDE_REFRESH_OUTCOME_UNKNOWN") from exc
            if status != 200:
                state = (
                    "REAUTH_REQUIRED" if status in {400, 401, 403} else "CLAUDE_REFRESH_REJECTED"
                )
                self.profile.save(
                    ClaudeCredential(
                        saved.access_token,
                        saved.refresh_token,
                        saved.expires_at,
                        saved.generation,
                        saved.profile_id,
                        state,
                    )
                )
                raise ClaudeOAuthHold(state)
            try:
                access, refresh, expires_at = self._token(
                    data, previous_refresh=saved.refresh_token
                )
            except ClaudeOAuthHold as exc:
                self.profile.save(
                    ClaudeCredential(
                        saved.access_token,
                        saved.refresh_token,
                        saved.expires_at,
                        saved.generation,
                        saved.profile_id,
                        "CLAUDE_REFRESH_OUTCOME_UNKNOWN",
                    )
                )
                raise ClaudeOAuthHold("CLAUDE_REFRESH_OUTCOME_UNKNOWN") from exc
            rotated = ClaudeCredential(
                access, refresh, expires_at, uuid.uuid4().hex, saved.profile_id
            )
            self.profile.save(rotated)
            return ClaudeSession(access, rotated.profile_id, rotated.generation, expires_at)

    @contextmanager
    def dispatch_gate(self, session: ClaudeSession) -> Generator[None]:
        """Fence generation from final check through the first physical HTTP send."""
        with self.profile.lock(timeout_seconds=8):
            self._require_session(session)
            yield

    def _require_session(self, session: ClaudeSession) -> None:
        current = self.profile.read()
        if (
            current is None
            or current.state != "READY"
            or current.profile_id != session.profile_id
            or current.generation != session.generation
            or not hmac.compare_digest(current.access_token, session.access_token)
            or current.expires_at <= time.time() + 5
        ):
            raise ClaudeOAuthHold("CLAUDE_AUTH_SNAPSHOT_CHANGED")

    @asynccontextmanager
    async def dispatch_gate_async(
        self, session: ClaudeSession, *, timeout_seconds: float = 8
    ) -> AsyncGenerator[None]:
        async with self.profile.async_lock(timeout_seconds=timeout_seconds):
            await asyncio.to_thread(self._require_session, session)
            yield

    def close(self) -> None:
        with self._lock:
            attempt = self._attempt
            if attempt is not None and attempt.login_state == "PENDING":
                attempt.cancel.set()
                attempt.login_state, attempt.reason_code = "CANCELLED", "CLAUDE_LOGIN_CANCELLED"
                if attempt.timer is not None:
                    attempt.timer.cancel()
                self._stop_listener(attempt)


_BROKERS: dict[Path, ClaudeOAuthBroker] = {}
_BROKER_USERS: dict[Path, int] = {}
_BROKERS_LOCK = threading.RLock()


def broker_for_workspace(
    workspace: Path,
    *,
    client_id: str | None = None,
    scope: str = "user:inference",
    transport: httpx.BaseTransport | None = None,
    browser_opener: Callable[[str], object] | None = None,
) -> ClaudeOAuthBroker:
    key = workspace.resolve()
    with _BROKERS_LOCK:
        broker = _BROKERS.get(key)
        if broker is None:
            broker = ClaudeOAuthBroker(
                key,
                client_id=client_id,
                scope=scope,
                transport=transport,
                browser_opener=browser_opener,
            )
            _BROKERS[key] = broker
        elif client_id is not None and broker.client_id != client_id:
            raise ClaudeOAuthHold("CLAUDE_CLIENT_IDENTITY_CONFLICT")
        return broker


def close_workspace_broker(workspace: Path) -> None:
    with _BROKERS_LOCK:
        key = workspace.resolve()
        if _BROKER_USERS.get(key, 0) > 0:
            raise ClaudeOAuthHold("CLAUDE_BROKER_RUNTIME_OWNED")
        broker = _BROKERS.pop(key, None)
    if broker is not None:
        broker.close()


def retain_workspace_broker(workspace: Path) -> ClaudeOAuthBroker:
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
            raise ClaudeOAuthHold("CLAUDE_BROKER_OWNER_MISSING")
        if users > 1:
            _BROKER_USERS[key] = users - 1
            return
        _BROKER_USERS.pop(key, None)
        broker = _BROKERS.pop(key, None)
    if broker is not None:
        broker.close()
