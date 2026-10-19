"""Slow shutdown never turns a completed read into failure or hides caller cancellation."""

from __future__ import annotations

import asyncio
from typing import Literal, cast

import pytest
from playwright.async_api import Browser

from thoth.adapters.connectors import anonymous_browser as rendering
from thoth.adapters.connectors.anonymous_browser_lifecycle import AnonymousBrowserLifecycle
from thoth.adapters.connectors.common import utc_now
from thoth.domain.connectors import ConnectorErrorCode, ConnectorFailure
from thoth.domain.web_acquisition import WebPage
from thoth.ports.web_reader import PublicReaderPort


class BrowserDouble:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.entered = asyncio.Event()
        self.released = asyncio.Event()
        self.finished = asyncio.Event()

    async def close(self) -> None:
        self.entered.set()
        try:
            if self.mode == "slow":
                await self.released.wait()
            elif self.mode == "error":
                raise RuntimeError("synthetic close failure")
            elif self.mode == "cancel":
                raise asyncio.CancelledError
        finally:
            self.finished.set()


class LifetimeDouble(AnonymousBrowserLifecycle):
    def __init__(self, mode: str = "slow", *, stop_stalls: bool = False) -> None:
        super().__init__()
        self.fake = BrowserDouble(mode)
        self.browser = cast(Browser, self.fake)
        self.stopped = asyncio.Event()
        self.stop_finished = asyncio.Event()
        self.stop_stalls = stop_stalls
        self.launch_error: Exception | None = None
        self._stop_driver = self.stop

    async def launch(self, timeout: float) -> Browser:
        if self.launch_error is not None:
            self.browser = None
            raise self.launch_error
        return cast(Browser, self.fake)

    async def stop(self) -> None:
        self.stopped.set()
        try:
            if self.stop_stalls:
                await asyncio.Event().wait()
            self.fake.released.set()
        finally:
            self.stop_finished.set()


class Renderer(rendering.AnonymousChromiumReader):
    def __init__(self, mode: Literal["return", "fail", "wait"] = "return") -> None:
        super().__init__(cast(PublicReaderPort, object()))
        self.mode = mode
        self.read_started = asyncio.Event()
        self.read_error = ValueError("synthetic read failure")

    async def _render(
        self, page: WebPage, browser: Browser, *, max_bytes: int, timeout: float
    ) -> WebPage:
        self.read_started.set()
        if self.mode == "fail":
            raise self.read_error
        if self.mode == "wait":
            await asyncio.Event().wait()
        return page


def source() -> WebPage:
    return WebPage(
        requested_uri="https://fixture.example.org/read",
        final_uri="https://fixture.example.org/read",
        media_type="text/html",
        raw=b"<p>Captured evidence</p>",
        retrieved_at=utc_now(),
    )


def install(monkeypatch: pytest.MonkeyPatch, lifetime: LifetimeDouble) -> None:
    monkeypatch.setattr(rendering, "AnonymousBrowserLifecycle", lambda: lifetime)


@pytest.mark.parametrize("mode", ("slow", "error", "cancel", "normal"))
async def test_completed_page_survives_cleanup(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, mode: str
) -> None:
    lifetime = LifetimeDouble(mode)
    install(monkeypatch, lifetime)
    page = source()
    assert await Renderer().render(page, max_bytes=1000, timeout=1) is page
    assert lifetime.stopped.is_set() and lifetime.stop_finished.is_set()
    assert lifetime.fake.finished.is_set()
    if mode == "slow":
        assert "ANONYMOUS_BROWSER_CLOSE_DELAYED" in caplog.text
    elif mode in {"error", "cancel"}:
        assert "ANONYMOUS_BROWSER_CLOSE_FAILED" in caplog.text


@pytest.mark.parametrize("stop_stalls", (False, True))
async def test_read_failure_is_not_overwritten(
    monkeypatch: pytest.MonkeyPatch, stop_stalls: bool
) -> None:
    lifetime = LifetimeDouble(stop_stalls=stop_stalls)
    install(monkeypatch, lifetime)
    reader = Renderer("fail")
    with pytest.raises(ValueError) as caught:
        await reader.render(source(), max_bytes=1000, timeout=1)
    assert caught.value is reader.read_error
    assert lifetime.stopped.is_set()


async def test_read_timeout_stays_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    lifetime = LifetimeDouble("normal")
    install(monkeypatch, lifetime)
    with pytest.raises(TimeoutError):
        await Renderer("wait").render(source(), max_bytes=1000, timeout=0.01)
    assert lifetime.stopped.is_set()


@pytest.mark.parametrize("during_cleanup", (False, True))
async def test_outer_cancel_and_repeated_cancel_propagate_after_cleanup(
    monkeypatch: pytest.MonkeyPatch, during_cleanup: bool
) -> None:
    lifetime = LifetimeDouble()
    install(monkeypatch, lifetime)
    reader = Renderer("return" if during_cleanup else "wait")
    task = asyncio.create_task(reader.render(source(), max_bytes=1000, timeout=1))
    await (lifetime.fake.entered if during_cleanup else reader.read_started).wait()
    task.cancel()
    await lifetime.fake.entered.wait()
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert lifetime.stop_finished.is_set() and lifetime.fake.finished.is_set()


async def test_partial_launch_failure_stops_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    lifetime = LifetimeDouble()
    failure = ValueError("synthetic launch failure")
    lifetime.launch_error = failure
    install(monkeypatch, lifetime)
    with pytest.raises(ValueError) as caught:
        await Renderer().render(source(), max_bytes=1000, timeout=1)
    assert caught.value is failure
    assert lifetime.stop_finished.is_set()


@pytest.mark.parametrize("stop_fails", (False, True))
async def test_outer_cancel_during_driver_stop_is_not_swallowed(
    monkeypatch: pytest.MonkeyPatch, stop_fails: bool
) -> None:
    lifetime = LifetimeDouble("normal")
    entered, release = asyncio.Event(), asyncio.Event()

    async def stop() -> None:
        entered.set()
        await release.wait()
        if stop_fails:
            raise RuntimeError("synthetic stop failure")
        await lifetime.stop()

    monkeypatch.setattr(lifetime, "_stop_driver", stop)
    install(monkeypatch, lifetime)
    task = asyncio.create_task(Renderer().render(source(), max_bytes=1000, timeout=1))
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_unconfirmed_cleanup_is_not_silently_reported_as_success(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    lifetime = LifetimeDouble("normal", stop_stalls=True)
    install(monkeypatch, lifetime)
    with pytest.raises(ConnectorFailure) as caught:
        await Renderer().render(source(), max_bytes=1000, timeout=1)
    assert caught.value.code == ConnectorErrorCode.DRIVER_ERROR
    assert "ANONYMOUS_BROWSER_CLEANUP_UNCONFIRMED" in str(caught.value)
    assert "ANONYMOUS_BROWSER_CLEANUP_UNCONFIRMED" in caplog.text
    await asyncio.sleep(0)
    assert lifetime.stop_finished.is_set()
