"""Keep a long model call fenced and its lease renewed, within one caller deadline."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import cast

from thoth.application.services.connector_io import retain_detached

BOUNDARY_POLL_SECONDS = 5.0
CANCEL_DRAIN_SECONDS = 2.0


async def await_current_model[T](
    invoke: Callable[[], Awaitable[T]], check: Callable[[], None], timeout: float | None
) -> T:
    async def run() -> T:
        return await invoke()

    task = asyncio.create_task(run())
    try:
        async with asyncio.timeout(timeout):
            while not task.done():
                await asyncio.wait({task}, timeout=BOUNDARY_POLL_SECONDS)
                check()
            return task.result()
    finally:
        if not task.done():
            task.cancel()
            # A broken adapter must not turn a finite research deadline into an
            # infinite cancellation wait. Late output is never returned to the caller.
            done, _ = await asyncio.wait({task}, timeout=CANCEL_DRAIN_SECONDS)
            if not done:
                retain_detached(cast(asyncio.Task[object], task))
            elif not task.cancelled():
                task.exception()
