"""Close a transport resource after an attempt without letting a second cancel cut the close.

Shared by the HTTP model transports. An attempt ends by a result, a hold or a cancel, and then its
response and client are closed. That close can be cut by another cancellation (the attempt's own
deadline expiring during cleanup, a second caller cancel), which leaves the stream open. It can also
hang. `close_within` runs a close to its end although cancelled again, for a bounded time, and
hands a cancel that arrived back to the caller instead of dropping it.
"""

import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass

# Each close (the response, then the client) may take this long once the attempt is over. Past it
# the close is given up, the observation says so, and the attempt's own outcome is raised as it
# was. The two closes together stay inside the 2 s the caller waits for a cancelled attempt.
CLEANUP_SECONDS = 1.0

_UNFINISHED_CLOSES: set[asyncio.Future[None]] = set()


@dataclass(frozen=True)
class CloseResult:
    finished: bool
    # A cancellation that arrived while the close ran. It is handed back, never dropped.
    cancelled: asyncio.CancelledError | None


async def close_within(close: Awaitable[None], limit: float) -> CloseResult:
    """Let a close run to its end although this task is cancelled again, but only for `limit`.

    The close runs as its own task behind a shield, so a cancel that lands while it runs (the
    attempt's deadline expiring during cleanup, a second caller cancel) cannot cut it. This task
    keeps waiting for it. A cancel that arrived is returned for the caller to raise once the close
    is over; it is not swallowed. A close that has not finished at `limit` is cancelled and reported
    as unfinished, so cleanup never keeps an attempt alive longer than `limit`.
    """
    task = asyncio.ensure_future(close)
    loop = asyncio.get_running_loop()
    end = loop.time() + limit
    cancelled: asyncio.CancelledError | None = None
    try:
        while not task.done():
            remaining = end - loop.time()
            if remaining <= 0:
                break
            try:
                await asyncio.wait_for(asyncio.shield(task), remaining)
            except TimeoutError:
                break
            except asyncio.CancelledError as exc:
                cancelled = exc
            except Exception:
                if cancelled is not None:
                    raise cancelled from None
                raise
    finally:
        if not task.done():
            task.cancel()
            # A close that ignores its cancel stays referenced until it ends.
            _UNFINISHED_CLOSES.add(task)
            task.add_done_callback(_UNFINISHED_CLOSES.discard)
    return CloseResult(task.done() and not task.cancelled(), cancelled)
