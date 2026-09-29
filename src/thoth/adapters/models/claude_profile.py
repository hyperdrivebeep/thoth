"""THOTH workspace-owned Claude OAuth secret and cross-process rotation lock."""

from __future__ import annotations

import asyncio
import csv
import hashlib
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
from typing import BinaryIO, cast

from thoth.ports.model import ModelExecutionHold


class ClaudeProfileHold(ModelExecutionHold):
    pass


@dataclass(frozen=True, repr=False)
class ClaudeCredential:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_at: float
    generation: str
    profile_id: str
    state: str = "READY"


class _HeldLock:
    def __init__(self, stream: BinaryIO) -> None:
        self.stream = stream

    def release(self) -> None:
        try:
            self.stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        finally:
            self.stream.close()


class ClaudeProfile:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.root = self.workspace / "model-registry" / "claude-oauth-profile"
        self.auth_path = self.root / "auth.json"
        self.profile_id = (
            "claude-profile:" + hashlib.sha256(str(self.root).encode("utf-8")).hexdigest()[:24]
        )

    def _guard(self, path: Path) -> None:
        if not path.resolve().is_relative_to(self.workspace) or path.is_symlink():
            raise ClaudeProfileHold("CLAUDE_PROFILE_BOUNDARY_INVALID")

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
                raise ClaudeProfileHold("CLAUDE_PROFILE_PERMISSION_UNAVAILABLE") from exc
            rows = list(csv.reader(io.StringIO(who.stdout)))
            sid = rows[0][1] if who.returncode == 0 and len(rows) == 1 and len(rows[0]) == 2 else ""
            if not sid.startswith("S-1-"):
                raise ClaudeProfileHold("CLAUDE_PROFILE_PERMISSION_UNAVAILABLE")
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
                raise ClaudeProfileHold("CLAUDE_PROFILE_PERMISSION_UNAVAILABLE") from exc
            if result.returncode != 0:
                raise ClaudeProfileHold("CLAUDE_PROFILE_PERMISSION_UNAVAILABLE")
        else:
            try:
                os.chmod(path, 0o700 if directory else 0o600)
            except OSError as exc:
                raise ClaudeProfileHold("CLAUDE_PROFILE_PERMISSION_UNAVAILABLE") from exc

    def prepare(self) -> None:
        self._guard(self.workspace / "model-registry")
        self._guard(self.root)
        try:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError as exc:
            raise ClaudeProfileHold("CLAUDE_PROFILE_BOUNDARY_INVALID") from exc
        self._private(self.root, directory=True)

    @contextmanager
    def lock(self, *, timeout_seconds: float = 8) -> Generator[None]:
        self.prepare()
        path = self.root / "owner.lock"
        self._guard(path)
        with path.open("a+b") as stream:
            self._private(path)
            if stream.seek(0, os.SEEK_END) == 0:
                stream.write(b"\0")
                stream.flush()
            deadline = time.monotonic() + timeout_seconds
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
                    if time.monotonic() >= deadline:
                        raise ClaudeProfileHold("CLAUDE_PROFILE_BUSY") from exc
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

    def _try_async_lock_once(self) -> _HeldLock | None:
        stream = (self.root / "owner.lock").open("r+b")
        try:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return _HeldLock(stream)
        except OSError:
            stream.close()
            return None
        except BaseException:
            stream.close()
            raise

    @staticmethod
    async def _finish_shielded[T](task: asyncio.Task[T]) -> T:
        while True:
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                if task.done():
                    return task.result()

    @asynccontextmanager
    async def async_lock(self, *, timeout_seconds: float = 8) -> AsyncGenerator[None]:
        """One nonblocking try per await; cancelled acquisition cannot keep a late lock."""
        prepare = asyncio.create_task(asyncio.to_thread(self._prepare_async_lock_file))
        try:
            await asyncio.shield(prepare)
        except asyncio.CancelledError:
            await self._finish_shielded(prepare)
            raise
        deadline = time.monotonic() + timeout_seconds
        held: _HeldLock | None = None
        while held is None:
            probe = asyncio.create_task(asyncio.to_thread(self._try_async_lock_once))
            try:
                held = await asyncio.shield(probe)
            except asyncio.CancelledError:
                acquired = await self._finish_shielded(probe)
                if acquired is not None:
                    acquired.release()
                raise
            if held is not None:
                break
            if time.monotonic() >= deadline:
                raise ClaudeProfileHold("CLAUDE_PROFILE_BUSY")
            await asyncio.sleep(min(0.05, deadline - time.monotonic()))
        try:
            yield
        finally:
            held.release()

    def read(self) -> ClaudeCredential | None:
        self._guard(self.auth_path)
        try:
            if self.auth_path.stat().st_size > 16_384:
                raise ClaudeProfileHold("CLAUDE_AUTH_SCHEMA_UNSUPPORTED")
            raw: object = json.loads(self.auth_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise ClaudeProfileHold("CLAUDE_AUTH_SNAPSHOT_UNAVAILABLE") from exc
        if not isinstance(raw, dict):
            raise ClaudeProfileHold("CLAUDE_AUTH_SCHEMA_UNSUPPORTED")
        value = cast(dict[str, object], raw)
        if (
            type(value.get("schema_version")) is not int
            or value["schema_version"] != 1
            or any(
                not isinstance(value.get(key), str) or not value.get(key)
                for key in ("access_token", "refresh_token", "generation", "profile_id", "state")
            )
            or not isinstance(value.get("expires_at"), (int, float))
            or isinstance(value.get("expires_at"), bool)
            or value.get("profile_id") != self.profile_id
        ):
            raise ClaudeProfileHold("CLAUDE_AUTH_SCHEMA_UNSUPPORTED")
        return ClaudeCredential(
            cast(str, value["access_token"]),
            cast(str, value["refresh_token"]),
            float(cast(float, value["expires_at"])),
            cast(str, value["generation"]),
            self.profile_id,
            cast(str, value["state"]),
        )

    def save(self, credential: ClaudeCredential) -> None:
        if credential.profile_id != self.profile_id:
            raise ClaudeProfileHold("CLAUDE_PROFILE_IDENTITY_MISMATCH")
        self.prepare()
        self._guard(self.auth_path)
        name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=self.root, prefix=".claude-", delete=False
            ) as stream:
                name = stream.name
                json.dump(
                    {
                        "schema_version": 1,
                        "access_token": credential.access_token,
                        "refresh_token": credential.refresh_token,
                        "expires_at": credential.expires_at,
                        "generation": credential.generation,
                        "profile_id": credential.profile_id,
                        "state": credential.state,
                    },
                    stream,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            temporary = Path(name)
            self._private(temporary)
            os.replace(temporary, self.auth_path)
            self._private(self.auth_path)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ClaudeProfileHold("CLAUDE_AUTH_SAVE_FAILED") from exc
        finally:
            if name is not None:
                Path(name).unlink(missing_ok=True)
