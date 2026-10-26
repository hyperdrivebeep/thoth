"""Deterministic completion-port states: ZERO alone never proves completed cleanup."""

from __future__ import annotations

import ctypes
import queue
import threading
import time
from collections import Counter
from ctypes import wintypes
from typing import Any

import pytest

from thoth.adapters.models.windows_job_completion import (
    JobCompletionTracker,
    _Accounting,  # pyright: ignore[reportPrivateUsage]
    associate_completion_port,
)


def handle_id(value: object) -> int:
    identifier = value.value if isinstance(value, wintypes.HANDLE) else value
    assert isinstance(identifier, int)
    return identifier


class CompletionKernel:
    def __init__(self, messages: list[tuple[int, int]], *, total: int = 2) -> None:
        self.messages: queue.Queue[tuple[int, int]] = queue.Queue()
        for message in messages:
            self.messages.put(message)
        self.total, self.active, self.error = total, 0, 258
        self.parent_signaled = True
        self.child_signaled = True
        self.open_error = 0
        self.foreign = False
        self.query_ok = True
        self.port_ok = True
        self.association_ok = True
        self.zero_delivered = threading.Event()
        self.port_closed = threading.Event()
        self.opened: list[int] = []
        self.closed: list[int] = []

    def GetCurrentProcess(self) -> int:
        return -1

    def DuplicateHandle(
        self,
        _source: object,
        _handle: object,
        _target: object,
        output: Any,
        _access: int,
        _inherit: bool,
        _options: int,
    ) -> bool:
        ctypes.cast(output, ctypes.POINTER(wintypes.HANDLE))[0] = 100
        self.opened.append(100)
        return True

    def CreateIoCompletionPort(self, *_args: object) -> int:
        return 20

    def SetInformationJobObject(self, *_args: object) -> bool:
        self.error = 5
        return self.association_ok

    def TerminateJobObject(self, *_args: object) -> bool:
        return True

    def OpenProcess(self, _access: int, _inherit: bool, pid: int) -> int | None:
        if self.open_error:
            self.error = self.open_error
            return None
        self.opened.append(100 + pid)
        return 100 + pid

    def IsProcessInJob(self, _process: object, _job: object, member: Any) -> bool:
        ctypes.cast(member, ctypes.POINTER(wintypes.BOOL))[0] = not self.foreign
        return True

    def GetQueuedCompletionStatus(
        self, _port: object, message: Any, key: Any, process: Any, timeout: int
    ) -> bool:
        if not self.port_ok:
            self.error = 6
            return False
        try:
            kind, pid = self.messages.get(timeout=timeout / 1000)
        except queue.Empty:
            self.error = 258
            return False
        ctypes.cast(message, ctypes.POINTER(wintypes.DWORD))[0] = kind
        ctypes.cast(key, ctypes.POINTER(ctypes.c_size_t))[0] = 1
        ctypes.cast(process, ctypes.POINTER(wintypes.LPVOID))[0] = pid
        if kind == 4:
            self.zero_delivered.set()
        return True

    def QueryInformationJobObject(
        self, _job: object, _kind: int, output: Any, _size: int, _length: object
    ) -> bool:
        if not self.query_ok:
            self.error = 5
            return False
        accounting = ctypes.cast(output, ctypes.POINTER(_Accounting)).contents
        accounting.TotalProcesses = self.total
        accounting.ActiveProcesses = self.active
        return True

    def WaitForSingleObject(self, handle: object, _timeout: int) -> int:
        return (
            0
            if (self.parent_signaled if handle_id(handle) == 100 else self.child_signaled)
            else 258
        )

    def CloseHandle(self, handle: object) -> bool:
        value = handle_id(handle)
        self.closed.append(value)
        if value == 20:
            self.port_closed.set()
        return True


def tracker(kernel: CompletionKernel, monkeypatch: pytest.MonkeyPatch) -> JobCompletionTracker:
    monkeypatch.setattr(ctypes, "get_last_error", lambda: kernel.error, raising=False)
    result = JobCompletionTracker(
        kernel, wintypes.HANDLE(10), wintypes.HANDLE(20), wintypes.HANDLE(1), 1
    )
    result.start()
    return result


def assert_closed(kernel: CompletionKernel, tracked: JobCompletionTracker) -> None:
    assert kernel.port_closed.wait(3)
    tracked._thread.join(3)  # pyright: ignore[reportPrivateUsage]
    assert tracked.closed
    assert Counter(kernel.closed) == Counter([*kernel.opened, 10, 20])


def test_zero_waits_for_actual_child_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    kernel = CompletionKernel([(6, 1), (6, 2), (4, 0)])
    kernel.child_signaled = False
    tracked = tracker(kernel, monkeypatch)
    deadline = time.monotonic() + 3
    tracked.request_stop(deadline)
    assert kernel.zero_delivered.wait(3)
    assert not tracked.confirmed
    kernel.child_signaled = True
    kernel.messages.put((0, 0))
    assert tracked.wait(deadline)
    assert_closed(kernel, tracked)


@pytest.mark.parametrize("messages", [[(6, 1), (4, 0), (6, 2), (4, 0)], [(6, 2), (4, 0)]])
def test_drains_new_members_and_keeps_the_assigned_root(
    monkeypatch: pytest.MonkeyPatch, messages: list[tuple[int, int]]
) -> None:
    kernel = CompletionKernel(messages)
    kernel.parent_signaled = False
    tracked = tracker(kernel, monkeypatch)
    deadline = time.monotonic() + 3
    tracked.request_stop(deadline)
    assert kernel.zero_delivered.wait(3)
    assert not tracked.confirmed
    kernel.parent_signaled = True
    kernel.messages.put((0, 0))
    assert tracked.wait(deadline)
    assert_closed(kernel, tracked)


@pytest.mark.parametrize(
    "failure", ["missing_new", "missing_zero", "access", "foreign", "query", "port", "active"]
)
def test_uncertain_membership_or_exit_fails_closed(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    messages = [(6, 1), (6, 2), (4, 0)]
    if failure == "missing_new":
        messages.remove((6, 2))
    if failure == "missing_zero":
        messages.remove((4, 0))
    kernel = CompletionKernel(messages)
    kernel.open_error = 5 if failure == "access" else 0
    kernel.foreign = failure == "foreign"
    kernel.query_ok = failure != "query"
    kernel.port_ok = failure != "port"
    kernel.active = 1 if failure == "active" else 0
    tracked = tracker(kernel, monkeypatch)
    deadline = time.monotonic() + 0.05
    tracked.request_stop(deadline)
    assert not tracked.wait(deadline)
    assert_closed(kernel, tracked)


def test_already_gone_member_is_not_a_missing_notification(monkeypatch: pytest.MonkeyPatch) -> None:
    kernel = CompletionKernel([(6, 1), (6, 2), (4, 0)])
    kernel.open_error = 87
    tracked = tracker(kernel, monkeypatch)
    deadline = time.monotonic() + 3
    tracked.request_stop(deadline)
    assert tracked.wait(deadline)
    assert_closed(kernel, tracked)


def test_port_association_failure_closes_the_new_port(monkeypatch: pytest.MonkeyPatch) -> None:
    kernel = CompletionKernel([])
    kernel.association_ok = False
    monkeypatch.setattr(ctypes, "get_last_error", lambda: kernel.error, raising=False)
    with pytest.raises(OSError, match="COMPLETION_ASSOCIATION_FAILED"):
        associate_completion_port(kernel, wintypes.HANDLE(10))
    assert kernel.closed == [20]
