"""Cancellation-safe async owner lock for existing workspace profile lock files."""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


@dataclass
class _HeldFileLock:
    stream: BinaryIO
    released: bool = False

    def release(self) -> None:
        if self.released:
            return
        self.released = True
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


def _try_lock(path: Path) -> _HeldFileLock | None:
    stream = path.open("r+b")
    try:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _HeldFileLock(stream)
    except OSError:
        stream.close()
        return None
    except BaseException:
        stream.close()
        raise


async def _finish_shielded[T](task: asyncio.Task[T]) -> T:
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.done():
                return task.result()


@asynccontextmanager
async def async_file_lock(
    path: Path,
    *,
    prepare: Callable[[], None],
    timeout_seconds: float,
    busy_error: Callable[[], Exception],
) -> AsyncGenerator[None]:
    """Probe once per await; a cancelled probe releases any late-acquired lock."""
    preparation = asyncio.create_task(asyncio.to_thread(prepare))
    try:
        await asyncio.shield(preparation)
    except asyncio.CancelledError:
        await _finish_shielded(preparation)
        raise
    deadline = time.monotonic() + timeout_seconds
    held: _HeldFileLock | None = None
    while held is None:
        probe = asyncio.create_task(asyncio.to_thread(_try_lock, path))
        try:
            held = await asyncio.shield(probe)
        except asyncio.CancelledError:
            acquired = await _finish_shielded(probe)
            if acquired is not None:
                acquired.release()
            raise
        if held is not None:
            break
        if time.monotonic() >= deadline:
            raise busy_error()
        await asyncio.sleep(min(0.05, deadline - time.monotonic()))
    try:
        yield
    finally:
        held.release()
