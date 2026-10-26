"""Windows suspended child bound to a kill-on-close Job before first instruction.

This primitive is used only for an unmodified local CLI process. It never
reads credentials or touches another process. Only its three stdio handles
are inherited by the new child; the Job Object is owned by the launcher.
"""

from __future__ import annotations

import asyncio
import ctypes
import msvcrt
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from ctypes import wintypes
from pathlib import Path
from typing import Any, BinaryIO, cast

from thoth.adapters.models.windows_job_completion import (
    JobCompletionTracker,
    associate_completion_port,
    configure_completion_api,
    duplicate_process_handle,
)

_CREATE_SUSPENDED = 0x00000004
_CREATE_NO_WINDOW = 0x08000000
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_STARTF_USESTDHANDLES = 0x00000100
_HANDLE_FLAG_INHERIT = 0x00000001
_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_INFINITE = 0xFFFFFFFF
_WAIT_OBJECT_0 = 0
_HANDLE_LIST_ATTRIBUTE = 0x00020002


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", wintypes.LPVOID),
        ("bInheritHandle", wintypes.BOOL),
    ]


class _StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _StartupInfoEx(ctypes.Structure):
    _fields_ = [
        ("StartupInfo", _StartupInfo),
        ("lpAttributeList", wintypes.LPVOID),
    ]


class _ProcessInformation(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_uint64)
        for name in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        )
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _api() -> Any:
    if sys.platform != "win32":
        raise RuntimeError("WINDOWS_JOB_OBJECT_UNAVAILABLE")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreatePipe.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        ctypes.POINTER(wintypes.HANDLE),
        ctypes.POINTER(_SecurityAttributes),
        wintypes.DWORD,
    ]
    kernel.CreatePipe.restype = wintypes.BOOL
    kernel.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
    kernel.SetHandleInformation.restype = wintypes.BOOL
    kernel.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.CreateProcessW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.BOOL,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPCWSTR,
        wintypes.LPVOID,
        ctypes.POINTER(_ProcessInformation),
    ]
    kernel.CreateProcessW.restype = wintypes.BOOL
    kernel.InitializeProcThreadAttributeList.argtypes = [
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel.InitializeProcThreadAttributeList.restype = wintypes.BOOL
    kernel.UpdateProcThreadAttribute.argtypes = [
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.c_size_t,
        wintypes.LPVOID,
        ctypes.c_size_t,
        wintypes.LPVOID,
        wintypes.LPVOID,
    ]
    kernel.UpdateProcThreadAttribute.restype = wintypes.BOOL
    kernel.DeleteProcThreadAttributeList.argtypes = [wintypes.LPVOID]
    kernel.DeleteProcThreadAttributeList.restype = None
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel.ResumeThread.restype = wintypes.DWORD
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateJobObject.restype = wintypes.BOOL
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateProcess.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    configure_completion_api(kernel)
    return kernel


def _check(ok: object, operation: str) -> None:
    if not ok:
        raise OSError(ctypes.get_last_error(), f"WINDOWS_JOB_{operation}_FAILED")


def _handle_value(handle: wintypes.HANDLE) -> int:
    if handle.value is None:
        raise RuntimeError("WINDOWS_JOB_NULL_HANDLE")
    return handle.value


class WindowsJobProcess:
    def __init__(
        self,
        kernel: Any,
        job: wintypes.HANDLE,
        process: wintypes.HANDLE,
        pid: int,
        stdin: BinaryIO,
        stdout: BinaryIO,
        stderr: BinaryIO,
        completion: JobCompletionTracker,
    ) -> None:
        self._kernel, self._job, self._process = kernel, job, process
        self._stdin, self._stdout, self._stderr = stdin, stdout, stderr
        self.pid = pid
        self._returncode: int | None = None
        self._closed = False
        self._completion = completion
        self._lock = threading.RLock()

    @property
    def returncode(self) -> int | None:
        return self._returncode

    @property
    def stdin(self) -> BinaryIO:
        return self._stdin

    @property
    def stdout(self) -> BinaryIO:
        return self._stdout

    @property
    def stderr(self) -> BinaryIO:
        return self._stderr

    def poll(self) -> int | None:
        """Exit code without blocking, or None while the child still runs."""
        with self._lock:
            exited = (
                self._returncode is None
                and not self._closed
                and self._kernel.WaitForSingleObject(self._process, 0) == _WAIT_OBJECT_0
            )
            if exited:
                exit_code = wintypes.DWORD()
                _check(
                    self._kernel.GetExitCodeProcess(self._process, ctypes.byref(exit_code)), "EXIT"
                )
                self._returncode = int(exit_code.value)
                self._close_handles()
            return self._returncode

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        def feed() -> None:
            try:
                if input:
                    self._stdin.write(input)
                    self._stdin.flush()
            finally:
                self._stdin.close()

        _written, stdout, stderr = await asyncio.gather(
            asyncio.to_thread(feed),
            asyncio.to_thread(self._stdout.read),
            asyncio.to_thread(self._stderr.read),
        )
        self._stdout.close()
        self._stderr.close()
        await self.wait()
        return stdout, stderr

    def kill(self) -> None:
        self._completion.request_stop(time.monotonic() + 3.0)

    async def stop_tree(self) -> bool:
        """Bound the blocking native confirmation by the same three-second deadline."""
        deadline = time.monotonic() + 3.0
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self.stop_and_confirm, deadline),
                max(0.0, deadline - time.monotonic()),
            )
        except (TimeoutError, OSError):
            return False

    def stop_and_confirm(self, deadline: float) -> bool:
        """Share one caller deadline across termination, membership and handle confirmation."""
        # The tracker records uncertainty; closing its job still requests termination.
        with suppress(OSError):
            self._completion.request_stop(deadline)
        confirmed = self._completion.wait(deadline)
        try:
            self.poll()
        except OSError:
            confirmed = False
        finally:
            self._close_handles()
        for stream in (self._stdin, self._stdout, self._stderr):
            try:
                stream.close()
            except (OSError, ValueError):
                confirmed = confirmed and stream.closed
        return (
            confirmed
            and all(s.closed for s in (self._stdin, self._stdout, self._stderr))
            and time.monotonic() <= deadline
        )

    async def wait(self) -> int:
        def wait_parent() -> int:
            with self._lock:
                if self._returncode is not None:
                    return self._returncode
                if self._closed:
                    raise OSError("WINDOWS_JOB_PROCESS_EXIT_UNCONFIRMED")
                handle = duplicate_process_handle(self._kernel, self._process)
            try:
                if self._kernel.WaitForSingleObject(handle, _INFINITE) != _WAIT_OBJECT_0:
                    raise OSError(ctypes.get_last_error(), "WINDOWS_JOB_WAIT_FAILED")
                exit_code = wintypes.DWORD()
                _check(self._kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)), "EXIT")
            finally:
                self._kernel.CloseHandle(handle)
            with self._lock:
                if self._returncode is None:
                    self._returncode = int(exit_code.value)
                self._close_handles()
                return self._returncode

        return await asyncio.to_thread(wait_parent)

    def _close_handles(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            with suppress(OSError):
                self._completion.request_stop(time.monotonic() + 3.0)
            self._kernel.CloseHandle(self._process)


def spawn_windows_job(
    argv: tuple[str, ...],
    env: Mapping[str, str],
    cwd: Path,
    *,
    _fault_at: str | None = None,
    _on_created: Callable[[int], None] | None = None,
) -> WindowsJobProcess:
    """Create suspended, bind to a kill-on-close job, then resume."""
    kernel = _api()
    attributes = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), None, True)
    handles: list[wintypes.HANDLE] = []

    def pipe() -> tuple[wintypes.HANDLE, wintypes.HANDLE]:
        reader, writer = wintypes.HANDLE(), wintypes.HANDLE()
        created = kernel.CreatePipe(
            ctypes.byref(reader), ctypes.byref(writer), ctypes.byref(attributes), 0
        )
        handles.extend(handle for handle in (reader, writer) if handle.value is not None)
        _check(created, "PIPE")
        return reader, writer

    job = wintypes.HANDLE()
    completion_port = wintypes.HANDLE()
    completion: JobCompletionTracker | None = None
    info = _ProcessInformation()
    attribute_list: wintypes.LPVOID | None = None
    attribute_initialized = False
    streams: list[BinaryIO] = []
    try:
        input_read, input_write = pipe()
        output_read, output_write = pipe()
        error_read, error_write = pipe()
        for parent_handle in (input_write, output_read, error_read):
            _check(kernel.SetHandleInformation(parent_handle, _HANDLE_FLAG_INHERIT, 0), "INHERIT")
        job = kernel.CreateJobObjectW(None, None)
        _check(job, "CREATE")
        limits = _ExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = _KILL_ON_JOB_CLOSE
        _check(
            kernel.SetInformationJobObject(
                job,
                _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(limits),
                ctypes.sizeof(limits),
            ),
            "LIMIT",
        )
        completion_port = associate_completion_port(kernel, job)
        list_size = ctypes.c_size_t(0)
        kernel.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(list_size))
        if list_size.value == 0:
            raise OSError(ctypes.get_last_error(), "WINDOWS_JOB_ATTRIBUTE_SIZE_FAILED")
        attribute_buffer = ctypes.create_string_buffer(list_size.value)
        attribute_list = ctypes.cast(attribute_buffer, wintypes.LPVOID)
        _check(
            kernel.InitializeProcThreadAttributeList(attribute_list, 1, 0, ctypes.byref(list_size)),
            "ATTRIBUTE_INIT",
        )
        attribute_initialized = True
        child_handles = (wintypes.HANDLE * 3)(
            _handle_value(input_read), _handle_value(output_write), _handle_value(error_write)
        )
        _check(
            kernel.UpdateProcThreadAttribute(
                attribute_list,
                0,
                _HANDLE_LIST_ATTRIBUTE,
                ctypes.cast(child_handles, wintypes.LPVOID),
                ctypes.sizeof(child_handles),
                None,
                None,
            ),
            "ATTRIBUTE_HANDLES",
        )
        startup = _StartupInfoEx()
        startup.StartupInfo.cb = ctypes.sizeof(startup)
        startup.StartupInfo.dwFlags = _STARTF_USESTDHANDLES
        (
            startup.StartupInfo.hStdInput,
            startup.StartupInfo.hStdOutput,
            startup.StartupInfo.hStdError,
        ) = (
            input_read,
            output_write,
            error_write,
        )
        startup.lpAttributeList = attribute_list
        command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
        environment = ctypes.create_unicode_buffer(
            "\0".join(f"{key}={value}" for key, value in sorted(env.items())) + "\0\0"
        )
        _check(
            kernel.CreateProcessW(
                argv[0],
                command_line,
                None,
                None,
                True,
                _CREATE_SUSPENDED
                | _CREATE_NO_WINDOW
                | _CREATE_UNICODE_ENVIRONMENT
                | _EXTENDED_STARTUPINFO_PRESENT,
                environment,
                str(cwd),
                ctypes.byref(startup),
                ctypes.byref(info),
            ),
            "PROCESS",
        )
        if _on_created is not None:
            _on_created(int(info.dwProcessId))
        if _fault_at == "assign":
            raise RuntimeError("SYNTHETIC_ASSIGN_FAILURE")
        _check(kernel.AssignProcessToJobObject(job, info.hProcess), "ASSIGN")
        completion = JobCompletionTracker(
            kernel, job, completion_port, info.hProcess, int(info.dwProcessId)
        )
        if _fault_at == "resume":
            raise RuntimeError("SYNTHETIC_RESUME_FAILURE")
        _check(kernel.ResumeThread(info.hThread) != _INFINITE, "RESUME")
        kernel.CloseHandle(info.hThread)
        info.hThread = wintypes.HANDLE()
        for child_handle in (input_read, output_write, error_write):
            kernel.CloseHandle(child_handle)
            handles.remove(child_handle)

        def adopt(handle: wintypes.HANDLE, flags: int, mode: str) -> BinaryIO:
            fd = msvcrt.open_osfhandle(_handle_value(handle), flags)
            handles.remove(handle)  # fd owns the OS handle from here.
            try:
                stream = cast(BinaryIO, os.fdopen(fd, mode))
            except BaseException:
                os.close(fd)
                raise
            streams.append(stream)
            return stream

        stdin = adopt(input_write, os.O_WRONLY, "wb")
        stdout = adopt(output_read, os.O_RDONLY, "rb")
        stderr = adopt(error_read, os.O_RDONLY, "rb")
        result = WindowsJobProcess(
            kernel, job, info.hProcess, int(info.dwProcessId), stdin, stdout, stderr, completion
        )
        completion.start()
        job = wintypes.HANDLE()
        completion_port = wintypes.HANDLE()
        info.hProcess = wintypes.HANDLE()
        return result
    except BaseException as failure:
        cleanup_failed = False
        if completion is not None:
            completion.abandon_before_start()
        if info.hProcess:
            kernel.TerminateProcess(info.hProcess, 1)
            cleanup_failed = kernel.WaitForSingleObject(info.hProcess, 3000) != _WAIT_OBJECT_0
            kernel.CloseHandle(info.hProcess)
        if info.hThread:
            kernel.CloseHandle(info.hThread)
        if job:
            kernel.CloseHandle(job)
        if completion_port:
            kernel.CloseHandle(completion_port)
        for stream in streams:
            stream.close()
        for handle in handles:
            kernel.CloseHandle(handle)
        if cleanup_failed:
            raise RuntimeError("WINDOWS_JOB_CLEANUP_UNCONFIRMED") from failure
        raise
    finally:
        if attribute_initialized and attribute_list is not None:
            kernel.DeleteProcThreadAttributeList(attribute_list)
