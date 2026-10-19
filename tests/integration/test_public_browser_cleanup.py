"""Prove SDK force-close with this render's real, deliberately suspended Chromium."""

from __future__ import annotations

import asyncio
import ctypes
import json
import os
import signal
import subprocess
from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Any, cast

import pytest
from playwright.async_api import Browser
from tests.integration.test_public_browser import SHELL, URI, FixtureReader

from thoth.adapters.connectors.anonymous_browser import AnonymousChromiumReader
from thoth.adapters.connectors.anonymous_browser_lifecycle import AnonymousBrowserLifecycle
from thoth.adapters.connectors.common import utc_now
from thoth.domain.web_acquisition import WebPage


class OwnedProcess:
    """Hold a Windows process handle so PID reuse cannot fool the exit assertion."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.handle: Any = None
        self.kernel: Any = None
        self.native: Any = None
        if os.name == "nt":
            from ctypes import wintypes

            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            self.kernel.OpenProcess.restype = wintypes.HANDLE
            self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            self.handle = self.kernel.OpenProcess(0x100000 | 0x1000 | 0x0800, False, pid)
            assert self.handle, (pid, ctypes.get_last_error())
            self.native = ctypes.WinDLL("ntdll")
            self.native.NtSuspendProcess.argtypes = [wintypes.HANDLE]
            self.native.NtResumeProcess.argtypes = [wintypes.HANDLE]

    def alive(self) -> bool:
        if os.name == "nt":
            return self.kernel.WaitForSingleObject(self.handle, 0) == 258
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            return False
        return True

    def suspend(self) -> None:
        if os.name == "nt":
            assert self.native.NtSuspendProcess(self.handle) == 0
        else:
            os.kill(self.pid, signal.SIGSTOP)

    def resume(self) -> None:
        if not self.alive():
            return
        if os.name == "nt":
            assert self.native.NtResumeProcess(self.handle) == 0
        else:
            os.kill(self.pid, signal.SIGCONT)

    def release(self) -> None:
        if os.name == "nt":
            self.kernel.CloseHandle(self.handle)


async def test_slow_close_preserves_page_and_force_stops_owned_processes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEBUG", "pw:browser")
    launch = AnonymousBrowserLifecycle.launch
    close = Browser.close
    observed: dict[str, Any] = {}
    processes: dict[int, OwnedProcess] = {}
    browser_process: OwnedProcess | None = None

    async def observe_launch(self: AnonymousBrowserLifecycle, timeout: float) -> Browser:
        nonlocal browser_process
        browser = await launch(self, timeout)
        # Private SDK access is confined to this proof, never used in product cleanup.
        driver_pid = int(cast(Any, browser)._impl_obj._connection._transport._proc.pid)
        session = await browser.new_browser_cdp_session()
        send = cast(Callable[[str], Awaitable[dict[str, Any]]], session.send)
        info = await send("SystemInfo.getProcessInfo")
        await session.detach()
        items = cast(list[dict[str, Any]], info["processInfo"])
        browser_pid = next(int(item["id"]) for item in items if item["type"] == "browser")
        for pid in {driver_pid, *(int(item["id"]) for item in items)}:
            processes[pid] = OwnedProcess(pid)
        browser_process = processes[browser_pid]
        stop: Callable[[], Awaitable[None]] | None = cast(Any, self)._stop_driver
        assert stop is not None
        observed.update(driver_pid=driver_pid, browser_pid=browser_pid, pids=sorted(processes))

        async def observe_stop() -> None:
            assert browser_process is not None
            observed["browser_alive_before_force"] = browser_process.alive()
            assert observed["browser_alive_before_force"]
            started = monotonic()
            try:
                await stop()
            finally:
                observed["force_cleanup_seconds"] = monotonic() - started
                observed["remaining_pids"] = [
                    pid for pid, proc in processes.items() if proc.alive()
                ]

        monkeypatch.setattr(self, "_stop_driver", observe_stop)
        return browser

    async def suspend_then_close(self: Browser, *, reason: str | None = None) -> None:
        assert browser_process is not None
        browser_process.suspend()
        observed["graceful_started"] = monotonic()
        await close(self, reason=reason)

    monkeypatch.setattr(AnonymousBrowserLifecycle, "launch", observe_launch)
    monkeypatch.setattr(Browser, "close", suspend_then_close)
    page = WebPage(
        requested_uri=URI, final_uri=URI, media_type="text/html", raw=SHELL, retrieved_at=utc_now()
    )
    try:
        result = await AnonymousChromiumReader(FixtureReader()).render(
            page, max_bytes=20000, timeout=5
        )
        assert b"Measured latency: 12 ms" in result.raw
        assert result.requested_uri == result.final_uri == URI
        observed["cleanup_total_seconds"] = monotonic() - observed["graceful_started"]
        assert observed["force_cleanup_seconds"] < 2
        assert observed["remaining_pids"] == []
        await asyncio.sleep(0)
    finally:
        observed["rescue_required"] = any(proc.alive() for proc in processes.values())
        print("BROWSER_CLEANUP " + json.dumps(observed, sort_keys=True))
        # A failing experiment must not leave its suspended browser behind. This
        # rescue is not counted as proof of product cleanup; the test still fails.
        if browser_process is not None and browser_process.alive():
            browser_process.resume()
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(browser_process.pid), "/T", "/F"],
                    check=False,
                    capture_output=True,
                    timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            else:
                os.killpg(browser_process.pid, signal.SIGKILL)
        for proc in processes.values():
            proc.release()
