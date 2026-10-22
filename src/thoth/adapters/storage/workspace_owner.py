"""One THOTH process per workspace.

The process that opens a workspace's database takes an exclusive lock on byte 0 of
`<workspace>/runtime.lock` (Windows `msvcrt.locking`, POSIX `fcntl.flock`). The operating system
releases it when the process ends, however it ends, so a leftover file never blocks a restart.
Runtimes inside the same process share the one lock by reference count. What the lock file says
(process id, start time, command) sits after byte 0 and is only a hint shown to the person who is
refused; it is never written to the database, the event journal or the diagnostics.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

LOCK_FILE = "runtime.lock"
_INFO_LIMIT = 4096


@dataclass(frozen=True)
class WorkspaceHolder:
    process_id: int | None
    started_at: str | None
    command: str | None


class WorkspaceInUse(RuntimeError):
    """Another THOTH process already has this workspace open."""

    def __init__(self, workspace: Path, holder: WorkspaceHolder | None) -> None:
        self.workspace = workspace
        self.holder = holder
        super().__init__("WORKSPACE_IN_USE")

    def _detail(self, korean: bool) -> str:
        holder = self.holder
        if holder is None:
            return ""
        parts: list[str] = []
        if holder.process_id is not None:
            parts.append(f"PID {holder.process_id}")
        if holder.started_at is not None:
            parts.append(f"{holder.started_at} 시작" if korean else f"started {holder.started_at}")
        if holder.command:
            parts.append(f"명령: {holder.command}" if korean else f"command: {holder.command}")
        return f" ({', '.join(parts)})" if parts else ""

    @property
    def message_ko(self) -> str:
        return (
            f"이 작업 공간은 이미 다른 THOTH가 사용 중입니다{self._detail(True)}. "
            f"그 THOTH를 먼저 종료한 뒤 다시 실행하세요. 작업 공간: {self.workspace}"
        )

    @property
    def message_en(self) -> str:
        return (
            f"This workspace is already in use by another THOTH{self._detail(False)}. "
            f"Close that one first, then run this again. Workspace: {self.workspace}"
        )


class WorkspaceOwnership:
    """One reference to the process-wide ownership of a workspace."""

    def __init__(self, key: str) -> None:
        self._key = key
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        _release(self._key)


@dataclass
class _Entry:
    count: int
    stream: BinaryIO


_OWNED: dict[str, _Entry] = {}
_GUARD = threading.Lock()


def _key(workspace: Path) -> str:
    return os.path.normcase(str(workspace.resolve()))


def _lock_byte(stream: BinaryIO) -> bool:
    try:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock_byte(stream: BinaryIO) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _own_command() -> str:
    name = Path(sys.argv[0]).name if sys.argv and sys.argv[0] else "thoth"
    sub = sys.argv[1] if len(sys.argv) > 1 else ""
    return f"{name} {sub}".strip() if sub.isidentifier() or sub.replace("-", "").isalpha() else name


def _write_info(stream: BinaryIO, command: str | None) -> None:
    info = {
        "pid": os.getpid(),
        "started_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "command": command or _own_command(),
    }
    stream.seek(1)
    stream.truncate()
    stream.write(json.dumps(info).encode("utf-8"))
    stream.flush()


def _read_holder(path: Path) -> WorkspaceHolder | None:
    try:
        with path.open("rb") as stream:
            stream.seek(1)
            raw = stream.read(_INFO_LIMIT)
        value = json.loads(raw.decode("utf-8"))
        pid, started, command = value.get("pid"), value.get("started_at"), value.get("command")
        return WorkspaceHolder(
            process_id=pid if isinstance(pid, int) else None,
            started_at=started if isinstance(started, str) else None,
            command=command if isinstance(command, str) else None,
        )
    except (OSError, ValueError, AttributeError):
        return None


def acquire_workspace_owner(workspace: Path, *, command: str | None = None) -> WorkspaceOwnership:
    """Own the workspace for this process, or raise WorkspaceInUse naming the other process."""
    root = workspace.resolve()
    key = _key(root)
    with _GUARD:
        entry = _OWNED.get(key)
        if entry is not None:
            entry.count += 1
            return WorkspaceOwnership(key)
        root.mkdir(parents=True, exist_ok=True)
        path = root / LOCK_FILE
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)
        stream = os.fdopen(descriptor, "r+b")
        try:
            if stream.seek(0, os.SEEK_END) == 0:
                stream.write(b"\0")
                stream.flush()
            if not _lock_byte(stream):
                raise WorkspaceInUse(root, _read_holder(path))
            _write_info(stream, command)
        except BaseException:
            stream.close()
            raise
        _OWNED[key] = _Entry(count=1, stream=stream)
        return WorkspaceOwnership(key)


def holds_workspace(workspace: Path) -> bool:
    with _GUARD:
        return _key(workspace) in _OWNED


def workspace_owner_key(workspace: Path) -> str:
    """Use the existing ownership identity for other process-local coordination."""
    return _key(workspace)


@contextmanager
def pin_workspace_owner(workspace: Path) -> Generator[bool]:
    """Retain only an already-owned lock; never acquire or create a lock file."""
    key = _key(workspace)
    with _GUARD:
        entry = _OWNED.get(key)
        if entry is not None:
            entry.count += 1
    try:
        yield entry is not None
    finally:
        if entry is not None:
            _release(key)


def _release(key: str) -> None:
    with _GUARD:
        entry = _OWNED.get(key)
        if entry is None:
            return
        entry.count -= 1
        if entry.count > 0:
            return
        del _OWNED[key]
        try:
            entry.stream.seek(1)
            entry.stream.truncate()
            entry.stream.flush()
        except OSError:
            pass
        finally:
            try:
                _unlock_byte(entry.stream)
            finally:
                entry.stream.close()
