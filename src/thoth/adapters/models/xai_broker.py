"""Bounded xAI device authorization and refresh for one THOTH workspace."""

from __future__ import annotations

import asyncio
import hmac
import threading
import time
import uuid
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

import httpx

from thoth.adapters.models.xai_profile import XaiCredential, XaiProfile, XaiProfileHold

# Public client settings observed in @code-yeongyu/senpi-ai 2026.9.26,
# dist/auth/oauth/xai.js SHA256 d34783560dc2ac75d6e717b248136eb75a902fbd53b3e56fb9f2ec50c6c793d7.
# This is an implementation reference, not a third-party support promise by xAI.
CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
SCOPE = "openid profile email offline_access grok-cli:access api:access"
DEVICE_URL = "https://auth.x.ai/oauth2/device/code"
TOKEN_URL = "https://auth.x.ai/oauth2/token"
_VERIFY_HOSTS = frozenset({"x.ai", "auth.x.ai", "grok.com", "accounts.x.ai"})


@dataclass
class _Login:
    login_id: str
    user_code: str
    verification_uri: str
    expires_at: float
    interval: float
    device_code: str = field(repr=False)
    state: str = "PENDING"
    reason_code: str | None = None
    cancel: threading.Event | None = None

    def public(self) -> dict[str, object]:
        return {
            "started": True,
            "provider": "xai",
            "kind": "xai_device_code",
            "login_id": self.login_id,
            "state": self.state,
            "reason_code": self.reason_code,
            "user_code": self.user_code,
            "verification_uri": self.verification_uri,
            "expires_at": int(self.expires_at),
        }


class XaiAuthBroker:
    def __init__(self, workspace: Path, *, transport: httpx.BaseTransport | None = None) -> None:
        self.profile = XaiProfile(workspace)
        self.transport = transport
        self._lock = threading.RLock()
        self._login: _Login | None = None

    def _post(self, url: str, data: dict[str, str]) -> tuple[int, dict[str, object]]:
        try:
            with httpx.Client(
                transport=self.transport, timeout=httpx.Timeout(5), follow_redirects=False
            ) as client:
                response = client.post(url, data=data, headers={"Accept": "application/json"})
                raw: object = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise XaiProfileHold("XAI_AUTH_NETWORK_OR_SCHEMA_FAILURE") from exc
        if not isinstance(raw, dict):
            raise XaiProfileHold("XAI_AUTH_SCHEMA_UNSUPPORTED")
        return response.status_code, cast(dict[str, object], raw)

    @staticmethod
    def _string(data: dict[str, object], key: str) -> str:
        value = data.get(key)
        if not isinstance(value, str) or not value:
            raise XaiProfileHold("XAI_AUTH_SCHEMA_UNSUPPORTED")
        return value

    @staticmethod
    def _duration(data: dict[str, object], key: str, default: float) -> float:
        value = data.get(key, default)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 < value <= 86400:
            raise XaiProfileHold("XAI_AUTH_SCHEMA_UNSUPPORTED")
        return float(value)

    def start_login(self) -> dict[str, object]:
        status, data = self._post(
            DEVICE_URL,
            {
                "client_id": CLIENT_ID,
                "scope": SCOPE,
                "referrer": "pi",
            },
        )
        if status != 200:
            reason = (
                "XAI_CLIENT_UNSUPPORTED"
                if data.get("error") in {"invalid_client", "unauthorized_client"}
                else "XAI_DEVICE_AUTH_REJECTED"
            )
            raise XaiProfileHold(reason)
        uri = self._string(data, "verification_uri")
        try:
            parsed = urlsplit(uri)
            port = parsed.port
        except ValueError as exc:
            raise XaiProfileHold("XAI_VERIFICATION_URI_UNTRUSTED") from exc
        if (
            parsed.scheme != "https"
            or parsed.hostname not in _VERIFY_HOSTS
            or parsed.username
            or parsed.password
            or port not in (None, 443)
            or parsed.query
            or parsed.fragment
        ):
            raise XaiProfileHold("XAI_VERIFICATION_URI_UNTRUSTED")
        duration = min(self._duration(data, "expires_in", 0), 900)
        login = _Login(
            uuid.uuid4().hex,
            self._string(data, "user_code"),
            uri,
            time.time() + duration,
            max(1, self._duration(data, "interval", 5)),
            self._string(data, "device_code"),
            cancel=threading.Event(),
        )
        with self._lock:
            if self._login is not None and self._login.cancel is not None:
                self._login.cancel.set()
                self._login.state = "CANCELLED"
                self._login.reason_code = "XAI_LOGIN_SUPERSEDED"
            self._login = login
            threading.Thread(
                target=self._poll, args=(login,), daemon=True, name="thoth-xai-device"
            ).start()
        return login.public()

    def login_status(self, login_id: str | None = None) -> dict[str, object]:
        with self._lock:
            login = self._login
            if login_id is not None and (login is None or login_id != login.login_id):
                raise XaiProfileHold("XAI_LOGIN_NOT_FOUND")
            if login is not None and (login_id is None or login_id == login.login_id):
                return login.public()
        credential = self.profile.read()
        if credential is not None and credential.generation != self.profile.generation():
            return {
                "provider": "xai",
                "kind": "xai_device_code",
                "state": "HOLD",
                "reason_code": "XAI_AUTH_GENERATION_MISMATCH",
            }
        connected = (
            credential is not None
            and credential.state == "READY"
            and credential.generation == self.profile.generation()
        )
        return {
            "provider": "xai",
            "kind": "xai_device_code",
            "state": "CONNECTED" if connected else "LOGIN_REQUIRED",
            "reason_code": None if connected else "XAI_LOGIN_REQUIRED",
        }

    def cancel_login(self, login_id: str) -> dict[str, object]:
        with self._lock:
            login = self._login
            if login is None or login.login_id != login_id:
                raise XaiProfileHold("XAI_LOGIN_NOT_FOUND")
            if login.state not in {"PENDING", "SLOW_DOWN"}:
                return login.public()
            if login.cancel is not None:
                login.cancel.set()
            login.state, login.reason_code = "CANCELLED", "XAI_LOGIN_CANCELLED"
            return login.public()

    def close(self) -> None:
        with self._lock:
            if self._login is not None and self._login.cancel is not None:
                self._login.cancel.set()

    def _poll(self, login: _Login) -> None:
        interval = login.interval
        try:
            while time.time() < login.expires_at:
                remaining = login.expires_at - time.time()
                if login.cancel is None or login.cancel.wait(min(interval, max(0, remaining))):
                    return
                if time.time() >= login.expires_at:
                    break
                status, data = self._post(
                    TOKEN_URL,
                    {
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                        "client_id": CLIENT_ID,
                        "device_code": login.device_code,
                    },
                )
                with self._lock:
                    if login.cancel.is_set() or self._login is not login:
                        return
                if status == 200:
                    credential = self._credential(data, login.login_id)
                    with self._lock, self.profile.lock():
                        if login.cancel.is_set() or self._login is not login:
                            return
                        self.profile.set_generation(login.login_id)
                        self.profile.save(credential)
                        login.state, login.reason_code = "CONNECTED", None
                    return
                error = data.get("error")
                if error == "authorization_pending":
                    login.state = "PENDING"
                elif error == "slow_down":
                    interval = max(interval + 5, self._duration(data, "interval", interval + 5))
                    login.state = "SLOW_DOWN"
                elif error in {"access_denied", "authorization_denied"}:
                    login.state, login.reason_code = "DENIED", "XAI_AUTH_DENIED"
                    return
                elif error == "expired_token":
                    login.state, login.reason_code = "EXPIRED", "XAI_AUTH_EXPIRED"
                    return
                else:
                    login.state, login.reason_code = "FAILED", "XAI_AUTH_REJECTED"
                    return
            if login.cancel is not None and not login.cancel.is_set():
                login.state, login.reason_code = "EXPIRED", "XAI_AUTH_EXPIRED"
        except XaiProfileHold as exc:
            if login.cancel is not None and not login.cancel.is_set():
                login.state, login.reason_code = "FAILED", str(exc)

    def _credential(
        self, data: dict[str, object], generation: str, previous_refresh: str | None = None
    ) -> XaiCredential:
        refresh = data.get("refresh_token", previous_refresh)
        if not isinstance(refresh, str) or not refresh:
            raise XaiProfileHold("XAI_AUTH_SCHEMA_UNSUPPORTED")
        return XaiCredential(
            self._string(data, "access_token"),
            refresh,
            time.time() + self._duration(data, "expires_in", 3600),
            generation,
        )

    def status(self) -> dict[str, object]:
        """Local read only. Never refresh from status or catalog paths."""
        try:
            credential = self.profile.read()
            generation = self.profile.generation()
        except XaiProfileHold as exc:
            return {
                "connected": False,
                "execution_eligible": False,
                "connection_state": "HOLD",
                "reason_code": str(exc),
            }
        if credential is not None and credential.generation != generation:
            return {
                "connected": False,
                "execution_eligible": False,
                "connection_state": "HOLD",
                "reason_code": "XAI_AUTH_GENERATION_MISMATCH",
            }
        if credential is None:
            state = self.login_status().get("state")
            return {
                "connected": False,
                "execution_eligible": False,
                "connection_state": state,
                "reason_code": "XAI_LOGIN_REQUIRED",
            }
        valid = credential.state == "READY" and credential.expires_at > time.time()
        return {
            "connected": credential.state == "READY",
            "execution_eligible": valid
            or (credential.state == "READY" and bool(credential.refresh_token)),
            "connection_state": "CONNECTED"
            if valid
            else "REFRESH_REQUIRED"
            if credential.state == "READY"
            else "HOLD",
            "reason_code": "EXECUTION_UNVERIFIED"
            if valid
            else "XAI_REFRESH_REQUIRED"
            if credential.state == "READY"
            else credential.state,
        }

    def execution_credential(self) -> XaiCredential:
        with self.profile.lock(timeout_seconds=8):
            credential = self.profile.read()
            if credential is None:
                raise XaiProfileHold("XAI_LOGIN_REQUIRED")
            if credential.generation != self.profile.generation():
                raise XaiProfileHold("XAI_AUTH_GENERATION_MISMATCH")
            if credential.state != "READY":
                raise XaiProfileHold(credential.state)
            if credential.expires_at > time.time() + 300:
                return credential
            try:
                status, data = self._post(
                    TOKEN_URL,
                    {
                        "grant_type": "refresh_token",
                        "client_id": CLIENT_ID,
                        "refresh_token": credential.refresh_token,
                    },
                )
            except XaiProfileHold as exc:
                self.profile.save(
                    XaiCredential(
                        credential.access_token,
                        credential.refresh_token,
                        credential.expires_at,
                        credential.generation,
                        "XAI_REFRESH_OUTCOME_UNKNOWN",
                    )
                )
                raise XaiProfileHold("XAI_REFRESH_OUTCOME_UNKNOWN") from exc
            if status != 200:
                state = (
                    "XAI_REAUTH_REQUIRED"
                    if status in {400, 401, 403}
                    else "XAI_REFRESH_RATE_LIMITED"
                    if status == 429
                    else "XAI_REFRESH_OUTCOME_UNKNOWN"
                )
                self.profile.save(
                    XaiCredential(
                        credential.access_token,
                        credential.refresh_token,
                        credential.expires_at,
                        credential.generation,
                        state,
                    )
                )
                raise XaiProfileHold(state)
            try:
                refreshed = self._credential(data, credential.generation, credential.refresh_token)
            except XaiProfileHold as exc:
                self.profile.save(
                    XaiCredential(
                        credential.access_token,
                        credential.refresh_token,
                        credential.expires_at,
                        credential.generation,
                        "XAI_REFRESH_OUTCOME_UNKNOWN",
                    )
                )
                raise XaiProfileHold("XAI_REFRESH_OUTCOME_UNKNOWN") from exc
            self.profile.save(refreshed)
            return refreshed

    def execution_token(self) -> str:
        return self.execution_credential().access_token

    @contextmanager
    def dispatch_gate(self, credential: XaiCredential) -> Generator[None]:
        """Hold the profile owner lock through the first physical HTTP send."""
        with self.profile.lock(timeout_seconds=8):
            self._check_dispatch_credential(credential)
            yield

    def _check_dispatch_credential(self, credential: XaiCredential) -> None:
        current = self.profile.read()
        if (
            current is None
            or current.state != "READY"
            or current.generation != credential.generation
            or current.generation != self.profile.generation()
            or current.expires_at <= time.time() + 5
            or not hmac.compare_digest(current.access_token, credential.access_token)
        ):
            raise XaiProfileHold("XAI_AUTH_SNAPSHOT_CHANGED")

    @asynccontextmanager
    async def dispatch_gate_async(self, credential: XaiCredential) -> AsyncGenerator[None]:
        async with self.profile.async_lock(timeout_seconds=8):
            await asyncio.to_thread(self._check_dispatch_credential, credential)
            yield


_BROKERS: dict[Path, XaiAuthBroker] = {}
_BROKER_USERS: dict[Path, int] = {}
_BROKER_LOCK = threading.RLock()


def broker_for_workspace(workspace: Path) -> XaiAuthBroker:
    key = workspace.resolve()
    with _BROKER_LOCK:
        return _BROKERS.setdefault(key, XaiAuthBroker(key))


def retain_workspace_broker(workspace: Path) -> XaiAuthBroker:
    key = workspace.resolve()
    with _BROKER_LOCK:
        broker = broker_for_workspace(key)
        _BROKER_USERS[key] = _BROKER_USERS.get(key, 0) + 1
        return broker


def release_workspace_broker(workspace: Path) -> None:
    key = workspace.resolve()
    with _BROKER_LOCK:
        users = _BROKER_USERS.get(key, 0)
        if users <= 0:
            raise XaiProfileHold("XAI_BROKER_OWNER_MISSING")
        if users > 1:
            _BROKER_USERS[key] = users - 1
            return
        _BROKER_USERS.pop(key, None)
        broker = _BROKERS.pop(key, None)
    if broker is not None:
        broker.close()
