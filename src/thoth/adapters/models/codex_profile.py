"""THOTH-owned Codex profile and pinned, read-only credential snapshot."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import AsyncGenerator, Callable, Generator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import uuid4

from thoth.adapters.models.async_file_lock import async_file_lock
from thoth.ports.model import ModelExecutionHold

PINNED_VERSION = "codex-cli 0.157.1"
PINNED_PACKAGE_VERSION = "0.157.1"
PINNED_PLATFORM_VERSION = "0.157.1-win32-x64"
PINNED_SOURCE_COMMIT = "36650394c5b38c2990ccf2a3457165ca3e9d9726"
APP_SERVER_COMMON_SOURCE_SHA256 = "27fe9ee0a155ee1e5f59860869277fd7179f22e151a38326738396b60d97ebea"
APP_SERVER_ACCOUNT_SOURCE_SHA256 = (
    "f52c03c9f98f8fd496824616ec1669954f35470cbd2ff15101ceb4b7e6a57b2ad"
)
WRAPPER_INTEGRITY = (
    "sha512-qJ/UZ0bmYP+/Umav1L9WpmtMYeA6q1+4r4qILSYOKQZhP7WRdjy"
    "TQWz3O0dTImZ9RT7AazZa85D87xRDHogcHw=="
)
PLATFORM_INTEGRITY = (
    "sha512-vgqs/VRXNwhLYMsZDgYfnRSXpRh5Nm782L8lacGskw86kOxbMaquvQKxku"
    "ZHUBrJA2XGcksB7rMUHy1XaCJgrA=="
)
NATIVE_RELATIVE = Path("vendor/x86_64-pc-windows-msvc/bin/codex.exe")
AUTH_READER_VERSION = "AuthDotJson-TokenData-v1"
CODEX_BACKEND_ORIGIN = "https://chatgpt.com"
CODEX_BACKEND_PATH = "/backend-api/codex/responses"


class CodexProfileHold(ModelExecutionHold):
    pass


@dataclass(frozen=True)
class CodexExecutableIdentity:
    path: Path
    package_root: Path
    version: str
    digest: str
    wrapper_integrity: str
    platform_integrity: str


@dataclass(frozen=True, repr=False)
class CodexAuthSnapshot:
    access_token: str
    account_id: str
    expires_at: datetime
    last_refresh: datetime
    content_digest: str


VersionRunner = Callable[[Path], str]


def read_codex_executable_version(path: Path) -> str:
    try:
        completed = subprocess.run(
            [str(path), "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CodexProfileHold("CODEX_EXECUTABLE_UNAVAILABLE") from exc
    if completed.returncode != 0:
        raise CodexProfileHold("CODEX_EXECUTABLE_UNAVAILABLE")
    return completed.stdout.strip()


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise CodexProfileHold("CODEX_AUTH_SCHEMA_UNSUPPORTED")
    mapping = cast(dict[object, object], value)
    if any(not isinstance(key, str) for key in mapping):
        raise CodexProfileHold("CODEX_AUTH_SCHEMA_UNSUPPORTED")
    return cast(dict[str, object], mapping)


def _date(value: object) -> datetime:
    if not isinstance(value, str):
        raise CodexProfileHold("CODEX_AUTH_SCHEMA_UNSUPPORTED")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CodexProfileHold("CODEX_AUTH_SCHEMA_UNSUPPORTED") from exc
    if parsed.tzinfo is None:
        raise CodexProfileHold("CODEX_AUTH_SCHEMA_UNSUPPORTED")
    return parsed.astimezone(UTC)


def _access_claims(token: str) -> tuple[datetime, str | None]:
    parts = token.split(".")
    if len(parts) != 3:
        raise CodexProfileHold("CODEX_ACCESS_EXPIRY_UNAVAILABLE")
    try:
        payload = _object(
            json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
        )
    except (ValueError, TypeError, UnicodeDecodeError) as exc:
        raise CodexProfileHold("CODEX_ACCESS_EXPIRY_UNAVAILABLE") from exc
    exp = payload.get("exp")
    if type(exp) is not int:
        raise CodexProfileHold("CODEX_ACCESS_EXPIRY_UNAVAILABLE")
    nested = payload.get("https://api.openai.com/auth")
    account = payload.get("chatgpt_account_id")
    if account is None and isinstance(nested, dict):
        account = cast(dict[str, object], nested).get("chatgpt_account_id")
    if account is not None and not isinstance(account, str):
        raise CodexProfileHold("CODEX_AUTH_SCHEMA_UNSUPPORTED")
    return datetime.fromtimestamp(exp, UTC), account


@dataclass(frozen=True)
class CodexProfile:
    workspace: Path
    root: Path

    @classmethod
    def for_workspace(cls, workspace: Path) -> CodexProfile:
        resolved = workspace.resolve()
        if not resolved.is_absolute():
            raise CodexProfileHold("CODEX_WORKSPACE_REQUIRED")
        root = resolved / "model-registry" / "codex-oauth-profile"
        if not root.resolve().is_relative_to(resolved):
            raise CodexProfileHold("CODEX_PROFILE_BOUNDARY_INVALID")
        return cls(resolved, root)

    @property
    def auth_path(self) -> Path:
        return self.root / "auth.json"

    @property
    def pin_path(self) -> Path:
        return self.root / "thoth-codex-pin.json"

    @property
    def login_pending_path(self) -> Path:
        return self.root / "thoth-login-pending.json"

    @property
    def refresh_hold_path(self) -> Path:
        return self.root / "thoth-refresh-hold.json"

    def refresh_hold_digest(self) -> str | None:
        path = self.refresh_hold_path
        if path.is_symlink():
            raise CodexProfileHold("CODEX_REFRESH_HOLD_UNAVAILABLE")
        try:
            if path.stat().st_size > 1024:
                raise CodexProfileHold("CODEX_REFRESH_HOLD_UNAVAILABLE")
            raw = _object(json.loads(path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return None
        except (OSError, ValueError, CodexProfileHold) as exc:
            raise CodexProfileHold("CODEX_REFRESH_HOLD_UNAVAILABLE") from exc
        digest = raw.get("auth_digest")
        if (
            raw.get("schema_version") != 1
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            raise CodexProfileHold("CODEX_REFRESH_HOLD_UNAVAILABLE")
        return digest

    def mark_refresh_hold(self, auth_digest: str) -> None:
        if re.fullmatch(r"[0-9a-f]{64}", auth_digest) is None:
            raise CodexProfileHold("CODEX_REFRESH_HOLD_UNAVAILABLE")
        path = self.refresh_hold_path
        if path.is_symlink():
            raise CodexProfileHold("CODEX_REFRESH_HOLD_UNAVAILABLE")
        temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            with temp.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps({"schema_version": 1, "auth_digest": auth_digest}))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temp, 0o600)
            os.replace(temp, path)
        except OSError as exc:
            raise CodexProfileHold("CODEX_REFRESH_HOLD_UNAVAILABLE") from exc
        finally:
            temp.unlink(missing_ok=True)

    def clear_refresh_hold(self) -> None:
        try:
            self.refresh_hold_path.unlink(missing_ok=True)
        except OSError as exc:
            raise CodexProfileHold("CODEX_REFRESH_HOLD_UNAVAILABLE") from exc

    @contextmanager
    def lock(self, *, timeout_seconds: float = 5) -> Generator[None]:
        """Bounded cross-process lock for this profile's auth owner operations."""
        if not self.root.is_dir() or self.root.is_symlink():
            raise CodexProfileHold("CODEX_PROFILE_BOUNDARY_INVALID")
        lock_path = self.root / "thoth-auth.lock"
        if lock_path.is_symlink():
            raise CodexProfileHold("CODEX_PROFILE_BOUNDARY_INVALID")
        deadline = time.monotonic() + timeout_seconds
        with lock_path.open("a+b") as stream:
            if stream.seek(0, os.SEEK_END) == 0:
                stream.write(b"\0")
                stream.flush()
            while True:
                stream.seek(0)
                try:
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise CodexProfileHold("CODEX_PROFILE_BUSY") from exc
                    time.sleep(0.05)
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _prepare_async_lock_file(self) -> None:
        if not self.root.is_dir() or self.root.is_symlink():
            raise CodexProfileHold("CODEX_PROFILE_BOUNDARY_INVALID")
        path = self.root / "thoth-auth.lock"
        if path.is_symlink():
            raise CodexProfileHold("CODEX_PROFILE_BOUNDARY_INVALID")
        with path.open("a+b") as stream:
            if stream.seek(0, os.SEEK_END) == 0:
                stream.write(b"\0")
                stream.flush()
        os.chmod(path, 0o600)

    @asynccontextmanager
    async def async_lock(self, *, timeout_seconds: float = 8) -> AsyncGenerator[None]:
        async with async_file_lock(
            self.root / "thoth-auth.lock",
            prepare=self._prepare_async_lock_file,
            timeout_seconds=timeout_seconds,
            busy_error=lambda: CodexProfileHold("CODEX_PROFILE_BUSY"),
        ):
            yield

    def pending_login(self) -> bool:
        try:
            raw = _object(json.loads(self.login_pending_path.read_text(encoding="utf-8")))
            started = _date(raw.get("started_at"))
            return datetime.now(UTC) - started < timedelta(minutes=5)
        except (OSError, ValueError, CodexProfileHold):
            return False

    def pending_login_id(self) -> str | None:
        if not self.pending_login():
            return None
        try:
            raw = _object(json.loads(self.login_pending_path.read_text(encoding="utf-8")))
        except (OSError, ValueError, CodexProfileHold):
            return None
        value = raw.get("login_id")
        return value if isinstance(value, str) and value else None

    def mark_login_pending(self, login_id: str | None = None) -> None:
        self.login_pending_path.write_text(
            json.dumps({"started_at": datetime.now(UTC).isoformat(), "login_id": login_id}) + "\n",
            encoding="utf-8",
        )
        os.chmod(self.login_pending_path, 0o600)

    def clear_login_pending(self) -> None:
        try:
            self.login_pending_path.unlink(missing_ok=True)
        except OSError as exc:
            raise CodexProfileHold("CODEX_PROFILE_CLEANUP_FAILED") from exc

    def executable(
        self, *, version_runner: VersionRunner = read_codex_executable_version
    ) -> CodexExecutableIdentity:
        if os.name != "nt":
            raise CodexProfileHold("CODEX_PINNED_PLATFORM_UNSUPPORTED")
        configured = os.environ.get("THOTH_CODEX_PACKAGE_ROOT")
        if configured:
            wrapper = Path(configured)
            if not wrapper.is_absolute():
                raise CodexProfileHold("CODEX_PACKAGE_ROOT_ABSOLUTE_REQUIRED")
        elif self.pin_path.is_file():
            try:
                pinned = _object(json.loads(self.pin_path.read_text(encoding="utf-8")))
                saved = pinned.get("package_root")
                if not isinstance(saved, str) or not Path(saved).is_absolute():
                    raise CodexProfileHold("UPDATE_REVIEW_REQUIRED")
                wrapper = Path(saved)
            except (OSError, ValueError) as exc:
                raise CodexProfileHold("UPDATE_REVIEW_REQUIRED") from exc
        else:
            shim = shutil.which("codex")
            if not shim:
                raise CodexProfileHold("CODEX_STANDALONE_NOT_INSTALLED")
            wrapper = next(
                (
                    parent / "node_modules" / "@openai" / "codex"
                    for parent in (Path(shim).parent, *Path(shim).parents)
                    if (parent / "node_modules" / "@openai" / "codex" / "package.json").is_file()
                ),
                Path(shim).parent / "node_modules" / "@openai" / "codex",
            )
        wrapper = wrapper.resolve()
        if not wrapper.is_dir() or wrapper.is_symlink():
            raise CodexProfileHold("CODEX_STANDALONE_PIN_UNAVAILABLE")
        nested = wrapper / "node_modules" / "@openai" / "codex-win32-x64"
        sibling = wrapper.parent / "codex-win32-x64"
        candidates = (nested, sibling)
        platform_root = next((item.resolve() for item in candidates if item.is_dir()), None)
        if platform_root is None or platform_root.is_symlink():
            raise CodexProfileHold("CODEX_STANDALONE_PIN_UNAVAILABLE")
        try:
            wrapper_meta = _object(
                json.loads((wrapper / "package.json").read_text(encoding="utf-8"))
            )
            platform_meta = _object(
                json.loads((platform_root / "package.json").read_text(encoding="utf-8"))
            )
        except (OSError, ValueError) as exc:
            raise CodexProfileHold("CODEX_STANDALONE_PIN_UNAVAILABLE") from exc
        if (
            wrapper_meta.get("name") != "@openai/codex"
            or wrapper_meta.get("version") != PINNED_PACKAGE_VERSION
            or platform_meta.get("version") != PINNED_PLATFORM_VERSION
            or platform_meta.get("name") not in {"@openai/codex", "@openai/codex-win32-x64"}
        ):
            raise CodexProfileHold("CODEX_STANDALONE_PIN_UNAVAILABLE")
        node_modules = next(
            (parent for parent in wrapper.parents if parent.name == "node_modules"), None
        )
        if node_modules is None:
            raise CodexProfileHold("CODEX_STANDALONE_PIN_UNAVAILABLE")
        lock_path = node_modules / ".package-lock.json"
        try:
            lock = _object(json.loads(lock_path.read_text(encoding="utf-8")))
            packages = _object(lock.get("packages"))
            wrapper_key = wrapper.relative_to(node_modules.parent).as_posix()
            platform_key = platform_root.relative_to(node_modules.parent).as_posix()
            wrapper_lock = _object(packages.get(wrapper_key))
            platform_lock = _object(packages.get(platform_key))
        except (OSError, ValueError, KeyError) as exc:
            raise CodexProfileHold("CODEX_PACKAGE_INTEGRITY_UNAVAILABLE") from exc
        if (
            wrapper_lock.get("integrity") != WRAPPER_INTEGRITY
            or platform_lock.get("integrity") != PLATFORM_INTEGRITY
        ):
            raise CodexProfileHold("CODEX_PACKAGE_INTEGRITY_MISMATCH")
        native = (platform_root / NATIVE_RELATIVE).resolve()
        if not native.is_relative_to(platform_root) or not native.is_file():
            raise CodexProfileHold("CODEX_STANDALONE_PIN_UNAVAILABLE")
        version = version_runner(native)
        if version != PINNED_VERSION:
            raise CodexProfileHold("CODEX_STANDALONE_PIN_UNAVAILABLE")
        digest = hashlib.sha256()
        with native.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return CodexExecutableIdentity(
            native, wrapper, version, digest.hexdigest(), WRAPPER_INTEGRITY, PLATFORM_INTEGRITY
        )

    def _pin(self, identity: CodexExecutableIdentity) -> dict[str, object]:
        return {
            "schema_version": AUTH_READER_VERSION,
            "executable": str(identity.path),
            "package_root": str(identity.package_root),
            "version": identity.version,
            "sha256": identity.digest,
            "wrapper_integrity": identity.wrapper_integrity,
            "platform_integrity": identity.platform_integrity,
            "source_commit": PINNED_SOURCE_COMMIT,
            "app_server_common_source_sha256": APP_SERVER_COMMON_SOURCE_SHA256,
            "app_server_account_source_sha256": APP_SERVER_ACCOUNT_SOURCE_SHA256,
            "backend_origin": CODEX_BACKEND_ORIGIN,
        }

    def prepare_login(self, identity: CodexExecutableIdentity) -> None:
        if self.root.exists() and self.root.is_symlink():
            raise CodexProfileHold("CODEX_PROFILE_BOUNDARY_INVALID")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        config = self.root / "config.toml"
        expected = 'cli_auth_credentials_store = "file"\n'
        if config.exists() and config.read_text(encoding="utf-8") != expected:
            raise CodexProfileHold("CODEX_PROFILE_CONFIG_CONFLICT")
        if not config.exists():
            config.write_text(expected, encoding="utf-8", newline="\n")
            os.chmod(config, 0o600)
        expected_pin = self._pin(identity)
        if self.pin_path.exists():
            self.check_pin(identity)
        else:
            self.pin_path.write_text(
                json.dumps(expected_pin, sort_keys=True) + "\n", encoding="utf-8"
            )
            os.chmod(self.pin_path, 0o600)

    def check_pin(self, identity: CodexExecutableIdentity) -> None:
        if self.root.is_symlink() or self.auth_path.is_symlink():
            raise CodexProfileHold("CODEX_PROFILE_BOUNDARY_INVALID")
        try:
            recorded = _object(json.loads(self.pin_path.read_text(encoding="utf-8")))
            config = (self.root / "config.toml").read_text(encoding="utf-8")
        except (OSError, ValueError) as exc:
            raise CodexProfileHold("UPDATE_REVIEW_REQUIRED") from exc
        if recorded != self._pin(identity) or config != 'cli_auth_credentials_store = "file"\n':
            raise CodexProfileHold("UPDATE_REVIEW_REQUIRED")

    def read_auth(
        self, *, refreshed_after: datetime | None = None, require_fresh: bool = True
    ) -> CodexAuthSnapshot:
        if self.auth_path.is_symlink():
            raise CodexProfileHold("CODEX_PROFILE_BOUNDARY_INVALID")
        try:
            before = self.auth_path.stat()
            if before.st_size > 128 * 1024:
                raise CodexProfileHold("CODEX_AUTH_SCHEMA_UNSUPPORTED")
            raw = self.auth_path.read_bytes()
            after = self.auth_path.stat()
            if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
                raise CodexProfileHold("CODEX_AUTH_SNAPSHOT_CHANGED")
            auth = _object(json.loads(raw.decode("utf-8-sig")))
            if auth.get("OPENAI_API_KEY") is not None:
                raise CodexProfileHold("CODEX_AUTH_MODE_MISMATCH")
            if auth.get("auth_mode") not in (None, "chatgpt", "Chatgpt"):
                raise CodexProfileHold("CODEX_AUTH_MODE_MISMATCH")
            tokens = _object(auth.get("tokens"))
            access, refresh, account = (
                tokens.get("access_token"),
                tokens.get("refresh_token"),
                tokens.get("account_id"),
            )
            if not all(isinstance(item, str) and item for item in (access, refresh, account)):
                raise CodexProfileHold("CODEX_AUTH_SCHEMA_UNSUPPORTED")
            access_token, account_id = cast(str, access), cast(str, account)
            last_refresh = _date(auth.get("last_refresh"))
            if refreshed_after is not None and last_refresh < refreshed_after - timedelta(
                seconds=5
            ):
                raise CodexProfileHold("CODEX_REFRESH_NOT_CONFIRMED")
            expires_at, token_account = _access_claims(access_token)
            if token_account is not None and token_account != account_id:
                raise CodexProfileHold("CODEX_ACCOUNT_MISMATCH")
            if require_fresh and expires_at <= datetime.now(UTC) + timedelta(minutes=5):
                raise CodexProfileHold("CODEX_ACCESS_EXPIRED")
            return CodexAuthSnapshot(
                access_token, account_id, expires_at, last_refresh, hashlib.sha256(raw).hexdigest()
            )
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            raise CodexProfileHold("CODEX_AUTH_SNAPSHOT_UNAVAILABLE") from exc
