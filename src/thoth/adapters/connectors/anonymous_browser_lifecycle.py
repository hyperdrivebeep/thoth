"""Own one render's browser and driver, independently of its reading deadline."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from thoth.domain.connectors import ConnectorErrorCode, ConnectorFailure

if TYPE_CHECKING:
    from playwright.async_api import Browser

_log = logging.getLogger(__name__)
_pending: set[asyncio.Task[None]] = set()


def _owned_task(action: Awaitable[None]) -> asyncio.Task[None]:
    async def invoke() -> None:
        await action

    task = asyncio.create_task(invoke())
    _pending.add(task)

    def finished(done: asyncio.Task[None]) -> None:
        _pending.discard(done)
        if not done.cancelled():
            done.exception()

    task.add_done_callback(finished)
    return task


class AnonymousBrowserLifecycle:
    def __init__(self) -> None:
        self.browser: Browser | None = None
        self._stop_driver: Callable[[], Awaitable[None]] | None = None

    async def launch(self, timeout: float) -> Browser:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise ConnectorFailure(
                ConnectorErrorCode.DRIVER_ERROR, "ANONYMOUS_BROWSER_NOT_INSTALLED"
            ) from exc
        manager = async_playwright()
        self._stop_driver = manager.__aexit__
        driver = await manager.start()
        self.browser = await driver.chromium.launch(
            headless=True,
            timeout=timeout * 1000,
            chromium_sandbox=True,
            args=["--disable-background-networking", "--disable-quic", "--no-proxy-server"],
        )
        return self.browser

    async def close(self) -> None:
        """Finish owned cleanup before propagating any caller cancellation."""
        cleanup = _owned_task(self._cleanup())
        cancelled = False
        while True:
            try:
                await asyncio.shield(cleanup)
                break
            except asyncio.CancelledError:
                if cleanup.cancelled():
                    raise
                cancelled = True
            except Exception:
                if cancelled:
                    raise asyncio.CancelledError from None
                raise
        if cancelled:
            raise asyncio.CancelledError

    async def _cleanup(self) -> None:
        if self._stop_driver is None:
            return
        closing = None if self.browser is None else _owned_task(self.browser.close())
        if closing is not None:
            done, _ = await asyncio.wait({closing}, timeout=2)
            if not done:
                _log.warning("ANONYMOUS_BROWSER_CLOSE_DELAYED")
            elif closing.cancelled() or closing.exception() is not None:
                _log.warning("ANONYMOUS_BROWSER_CLOSE_FAILED")
        # Closing this call's driver pipe re-enters SDK browser cleanup and forces
        # a browser whose graceful close is already in progress to exit.
        stopping = _owned_task(self._stop_driver())
        done, _ = await asyncio.wait({stopping}, timeout=2)
        confirmed = bool(done) and not stopping.cancelled() and stopping.exception() is None
        for task in (closing, stopping):
            if task is not None and not task.done():
                task.cancel()
        if not confirmed:
            _log.error("ANONYMOUS_BROWSER_CLEANUP_UNCONFIRMED")
            raise ConnectorFailure(
                ConnectorErrorCode.DRIVER_ERROR, "ANONYMOUS_BROWSER_CLEANUP_UNCONFIRMED"
            )
