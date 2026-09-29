"""A cancelled worker probe cannot retain a lock after the waiting task exits."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

import thoth.adapters.models.async_file_lock as lock_module
from thoth.adapters.models.async_file_lock import async_file_lock


@pytest.mark.asyncio
async def test_cancel_during_late_acquire_releases_lock_and_keeps_heartbeat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "owner.lock"
    started, release = threading.Event(), threading.Event()
    original = lock_module._try_lock  # pyright: ignore[reportPrivateUsage]
    probes = 0
    ticks = 0

    def prepare() -> None:
        with path.open("a+b") as stream:
            if stream.seek(0, 2) == 0:
                stream.write(b"\0")

    def delayed(target: Path):
        nonlocal probes
        probes += 1
        if probes == 1:
            started.set()
            assert release.wait(2)
        return original(target)

    monkeypatch.setattr(lock_module, "_try_lock", delayed)

    async def contender() -> None:
        async with async_file_lock(
            path,
            prepare=prepare,
            timeout_seconds=1,
            busy_error=lambda: RuntimeError("BUSY"),
        ):
            raise AssertionError("cancelled contender entered")

    task = asyncio.create_task(contender())
    assert await asyncio.wait_for(asyncio.to_thread(started.wait, 1), 2)

    async def heartbeat() -> None:
        nonlocal ticks
        for _ in range(10):
            ticks += 1
            await asyncio.sleep(0.01)

    await heartbeat()
    assert ticks == 10 and not task.done()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with async_file_lock(
        path,
        prepare=prepare,
        timeout_seconds=0.5,
        busy_error=lambda: RuntimeError("BUSY"),
    ):
        assert probes >= 2
