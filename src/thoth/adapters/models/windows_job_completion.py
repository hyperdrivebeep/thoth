"""Own a Job completion port and process handles until tree exit is confirmed."""

from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes
from typing import Any

_ACTIVE_ZERO = 4
_NEW_PROCESS = 6
_WAIT_TIMEOUT = 258
_ALREADY_GONE = 87
_PORT_KEY = 1


class _PortAssociation(ctypes.Structure):
    _fields_ = [("CompletionKey", wintypes.LPVOID), ("CompletionPort", wintypes.HANDLE)]


class _Accounting(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_int64),
        ("TotalKernelTime", ctypes.c_int64),
        ("ThisPeriodTotalUserTime", ctypes.c_int64),
        ("ThisPeriodTotalKernelTime", ctypes.c_int64),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


def configure_completion_api(kernel: Any) -> None:
    kernel.CreateIoCompletionPort.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.c_size_t,
        wintypes.DWORD,
    ]
    kernel.CreateIoCompletionPort.restype = wintypes.HANDLE
    kernel.GetQueuedCompletionStatus.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(ctypes.c_size_t),
        ctypes.POINTER(wintypes.LPVOID),
        wintypes.DWORD,
    ]
    kernel.GetQueuedCompletionStatus.restype = wintypes.BOOL
    kernel.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPVOID,
    ]
    kernel.QueryInformationJobObject.restype = wintypes.BOOL
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.IsProcessInJob.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.BOOL),
    ]
    kernel.IsProcessInJob.restype = wintypes.BOOL
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.DuplicateHandle.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    kernel.DuplicateHandle.restype = wintypes.BOOL


def duplicate_process_handle(kernel: Any, handle: wintypes.HANDLE) -> wintypes.HANDLE:
    result = wintypes.HANDLE()
    current = kernel.GetCurrentProcess()
    if not kernel.DuplicateHandle(current, handle, current, ctypes.byref(result), 0, False, 2):
        raise OSError(ctypes.get_last_error(), "WINDOWS_JOB_DUPLICATE_FAILED")
    return result


def associate_completion_port(kernel: Any, job: wintypes.HANDLE) -> wintypes.HANDLE:
    port = kernel.CreateIoCompletionPort(wintypes.HANDLE(-1), None, 0, 1)
    if not port:
        raise OSError(ctypes.get_last_error(), "WINDOWS_JOB_COMPLETION_PORT_FAILED")
    association = _PortAssociation(_PORT_KEY, port)
    if not kernel.SetInformationJobObject(
        job, 7, ctypes.byref(association), ctypes.sizeof(association)
    ):
        error = ctypes.get_last_error()
        kernel.CloseHandle(port)
        raise OSError(error, "WINDOWS_JOB_COMPLETION_ASSOCIATION_FAILED")
    return port


class JobCompletionTracker:
    """One reader owns the port; missing membership or unknown exit never means success."""

    def __init__(
        self,
        kernel: Any,
        job: wintypes.HANDLE,
        port: wintypes.HANDLE,
        process: wintypes.HANDLE,
        pid: int,
    ) -> None:
        self._kernel, self._job, self._port = kernel, job, port
        # The suspended, explicitly assigned root is known even if its NEW message is absent.
        self._handles = [duplicate_process_handle(kernel, process)]
        self._root_pid, self._root_notice = pid, False
        self._observed_processes = 1
        self._zero_seen = False
        self._uncertain = False
        self._stop_requested = False
        self._deadline: float | None = None
        self._lock = threading.Lock()
        self._finished = threading.Event()
        self._confirmed = False
        self._closed = False
        self._released = False
        self._thread = threading.Thread(target=self._run, name="thoth-job-completion", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def abandon_before_start(self) -> None:
        """The factory still owns job/port when it fails before starting this tracker."""
        for handle in self._handles:
            self._kernel.CloseHandle(handle)
        self._handles.clear()

    @property
    def confirmed(self) -> bool:
        return self._finished.is_set() and self._confirmed

    @property
    def closed(self) -> bool:
        return self._finished.is_set() and self._closed

    def request_stop(self, deadline: float) -> None:
        with self._lock:
            self._deadline = deadline if self._deadline is None else min(self._deadline, deadline)
            if self._released or self._stop_requested:
                return
            self._stop_requested = True
            if not self._kernel.TerminateJobObject(self._job, 1):
                self._uncertain = True
                raise OSError(ctypes.get_last_error(), "WINDOWS_JOB_TERMINATE_FAILED")

    def wait(self, deadline: float) -> bool:
        with self._lock:
            self._deadline = deadline if self._deadline is None else min(self._deadline, deadline)
            effective = self._deadline
        return self._finished.wait(max(0.0, effective - time.monotonic())) and self._confirmed

    def _message(self, timeout_ms: int) -> bool:
        message, key, process = wintypes.DWORD(), ctypes.c_size_t(), wintypes.LPVOID()
        if not self._kernel.GetQueuedCompletionStatus(
            self._port, ctypes.byref(message), ctypes.byref(key), ctypes.byref(process), timeout_ms
        ):
            if ctypes.get_last_error() == _WAIT_TIMEOUT:
                return False
            raise OSError(ctypes.get_last_error(), "WINDOWS_JOB_COMPLETION_READ_FAILED")
        if key.value != _PORT_KEY:
            self._uncertain = True
        elif message.value == _NEW_PROCESS:
            self._zero_seen = False
            self._track(int(process.value or 0))
        elif message.value == _ACTIVE_ZERO:
            self._zero_seen = True
        return True

    def _track(self, pid: int) -> None:
        if pid == self._root_pid and not self._root_notice:
            self._root_notice = True
            return
        self._observed_processes += 1
        if pid <= 0:
            self._uncertain = True
            return
        handle = self._kernel.OpenProcess(0x00100000 | 0x1000, False, pid)
        if not handle:
            if ctypes.get_last_error() != _ALREADY_GONE:
                self._uncertain = True
            return
        member = wintypes.BOOL()
        if (
            not self._kernel.IsProcessInJob(handle, self._job, ctypes.byref(member))
            or not member.value
        ):
            # A reused/foreign PID is never treated as a tracked member or terminated by PID.
            self._kernel.CloseHandle(handle)
            self._uncertain = True
            return
        self._handles.append(handle)

    def _complete(self) -> bool:
        if not self._zero_seen or self._uncertain:
            return False
        accounting = _Accounting()
        if not self._kernel.QueryInformationJobObject(
            self._job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None
        ):
            raise OSError(ctypes.get_last_error(), "WINDOWS_JOB_ACCOUNTING_FAILED")
        if accounting.ActiveProcesses != 0 or accounting.TotalProcesses != self._observed_processes:
            return False
        return all(self._kernel.WaitForSingleObject(handle, 0) == 0 for handle in self._handles)

    def _run(self) -> None:
        confirmed = False
        try:
            while True:
                with self._lock:
                    deadline = self._deadline
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    break
                self._message(50 if remaining is None else min(50, max(0, int(remaining * 1000))))
                # Drain NEW messages before comparing the lifetime accounting count.
                while self._message(0):
                    with self._lock:
                        deadline = self._deadline
                    if deadline is not None and time.monotonic() >= deadline:
                        break
                if self._complete():
                    confirmed = True
                    break
        except OSError:
            confirmed = False
        finally:
            with self._lock:
                closed = all([bool(self._kernel.CloseHandle(h)) for h in self._handles])
                self._handles.clear()
                closed = bool(self._kernel.CloseHandle(self._job)) and closed
                closed = bool(self._kernel.CloseHandle(self._port)) and closed
                self._closed = closed
                self._released = True
                self._confirmed = (
                    confirmed
                    and closed
                    and (self._deadline is None or time.monotonic() <= self._deadline)
                )
            self._finished.set()
