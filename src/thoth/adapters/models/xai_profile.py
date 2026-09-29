"""THOTH-owned xAI OAuth credentials; no external agent profile is consulted."""

from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import tempfile
import time
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from thoth.adapters.models.async_file_lock import async_file_lock
from thoth.ports.model import ModelExecutionHold


class XaiProfileHold(ModelExecutionHold):
    pass


@dataclass(frozen=True, repr=False)
class XaiCredential:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_at: float
    generation: str
    state: str = "READY"


class XaiProfile:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.root = self.workspace / "model-registry" / "xai-oauth-profile"
        self.auth_path = self.root / "auth.json"
        self.generation_path = self.root / "generation.json"

    def _guard(self, path: Path) -> None:
        if not path.resolve().is_relative_to(self.workspace) or path.is_symlink():
            raise XaiProfileHold("XAI_PROFILE_BOUNDARY_INVALID")

    def _private(self, path: Path, *, directory: bool = False) -> None:
        if os.name == "nt":
            try:
                who = subprocess.run(
                    ["whoami", "/user", "/fo", "csv", "/nh"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                    check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise XaiProfileHold("XAI_PROFILE_PERMISSION_UNAVAILABLE") from exc
            rows = list(csv.reader(io.StringIO(who.stdout)))
            sid = rows[0][1] if who.returncode == 0 and len(rows) == 1 and len(rows[0]) == 2 else ""
            if not sid.startswith("S-1-"):
                raise XaiProfileHold("XAI_PROFILE_PERMISSION_UNAVAILABLE")
            try:
                result = subprocess.run(
                    [
                        "icacls",
                        str(path),
                        "/inheritance:r",
                        "/grant:r",
                        f"*{sid}:(OI)(CI)F" if directory else f"*{sid}:F",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise XaiProfileHold("XAI_PROFILE_PERMISSION_UNAVAILABLE") from exc
            if result.returncode != 0:
                raise XaiProfileHold("XAI_PROFILE_PERMISSION_UNAVAILABLE")
        else:
            try:
                os.chmod(path, 0o700 if directory else 0o600)
            except OSError as exc:
                raise XaiProfileHold("XAI_PROFILE_PERMISSION_UNAVAILABLE") from exc

    def prepare(self) -> None:
        self._guard(self.workspace / "model-registry")
        self._guard(self.root)
        try:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError as exc:
            raise XaiProfileHold("XAI_PROFILE_BOUNDARY_INVALID") from exc
        self._private(self.root, directory=True)

    @contextmanager
    def lock(self, timeout_seconds: float = 5) -> Generator[None]:
        self.prepare()
        path = self.root / "owner.lock"
        self._guard(path)
        with path.open("a+b") as stream:
            self._private(path)
            if stream.seek(0, os.SEEK_END) == 0:
                stream.write(b"\0")
                stream.flush()
            end = time.monotonic() + timeout_seconds
            while True:
                try:
                    stream.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if time.monotonic() >= end:
                        raise XaiProfileHold("XAI_PROFILE_BUSY") from exc
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
        self.prepare()
        path = self.root / "owner.lock"
        self._guard(path)
        with path.open("a+b") as stream:
            if stream.seek(0, os.SEEK_END) == 0:
                stream.write(b"\0")
                stream.flush()
        self._private(path)

    @asynccontextmanager
    async def async_lock(self, *, timeout_seconds: float = 8) -> AsyncGenerator[None]:
        async with async_file_lock(
            self.root / "owner.lock",
            prepare=self._prepare_async_lock_file,
            timeout_seconds=timeout_seconds,
            busy_error=lambda: XaiProfileHold("XAI_PROFILE_BUSY"),
        ):
            yield

    def _read(self, path: Path) -> dict[str, object]:
        self._guard(path)
        try:
            if path.stat().st_size > 16_384:
                raise XaiProfileHold("XAI_AUTH_SCHEMA_UNSUPPORTED")
            raw: object = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            raise XaiProfileHold("XAI_AUTH_SNAPSHOT_UNAVAILABLE") from exc
        if not isinstance(raw, dict):
            raise XaiProfileHold("XAI_AUTH_SCHEMA_UNSUPPORTED")
        return cast(dict[str, object], raw)

    def _write(self, path: Path, value: dict[str, object]) -> None:
        self._guard(path)
        self.prepare()
        name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=self.root, prefix=".xai-", delete=False
            ) as stream:
                name = stream.name
                json.dump(value, stream, separators=(",", ":"), sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            temp = Path(name)
            self._private(temp)
            os.replace(temp, path)
            self._private(path)
        except (OSError, subprocess.SubprocessError) as exc:
            raise XaiProfileHold("XAI_AUTH_SAVE_FAILED") from exc
        finally:
            if name is not None:
                Path(name).unlink(missing_ok=True)

    def generation(self) -> str | None:
        value = self._read(self.generation_path).get("generation")
        return value if isinstance(value, str) and value else None

    def set_generation(self, generation: str) -> None:
        self._write(self.generation_path, {"schema_version": 1, "generation": generation})

    def read(self) -> XaiCredential | None:
        value = self._read(self.auth_path)
        if not value:
            return None
        if (
            value.get("schema_version") != 1
            or not all(
                isinstance(value.get(key), str) and value.get(key)
                for key in ("access_token", "refresh_token", "generation", "state")
            )
            or not isinstance(value.get("expires_at"), (int, float))
        ):
            raise XaiProfileHold("XAI_AUTH_SCHEMA_UNSUPPORTED")
        return XaiCredential(
            cast(str, value["access_token"]),
            cast(str, value["refresh_token"]),
            float(cast(float, value["expires_at"])),
            cast(str, value["generation"]),
            cast(str, value["state"]),
        )

    def save(self, credential: XaiCredential) -> None:
        self._write(
            self.auth_path,
            {
                "schema_version": 1,
                "access_token": credential.access_token,
                "refresh_token": credential.refresh_token,
                "expires_at": credential.expires_at,
                "generation": credential.generation,
                "state": credential.state,
            },
        )
