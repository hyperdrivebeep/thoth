"""Wait for real work to finish without putting a wall-clock limit on the work itself.

A fixed limit around real work (drain a research run, reach a model stage) measures the machine,
not the code: on a loaded runner the same run takes several times longer, and the limit fails a
correct run. These waits end when the awaited thing happens. They fail only when nothing the test
can observe has moved for `stall_seconds`, and the failure says what was last seen, so a real
hang is still caught and is reported with its state.
"""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress

POLL_SECONDS = 0.25
STALL_SECONDS = 30.0


async def wait_while_progressing[T](
    work: Awaitable[T],
    progress: Callable[[], object],
    *,
    label: str,
    stall_seconds: float = STALL_SECONDS,
) -> T:
    """Await `work`; `progress()` returns anything that changes while the work moves forward."""
    task = asyncio.ensure_future(work)
    loop = asyncio.get_running_loop()
    seen, since = progress(), loop.time()
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=POLL_SECONDS)
            if done:
                return task.result()
            current = progress()
            if current != seen:
                seen, since = current, loop.time()
            elif loop.time() - since > stall_seconds:
                raise AssertionError(
                    f"{label}: no progress for {stall_seconds} s; last seen {seen!r}"
                )
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


async def wait_for_event_while_progressing(
    event: asyncio.Event,
    progress: Callable[[], object],
    *,
    label: str,
    stall_seconds: float = STALL_SECONDS,
) -> None:
    await wait_while_progressing(event.wait(), progress, label=label, stall_seconds=stall_seconds)
