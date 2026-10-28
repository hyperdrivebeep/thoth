"""End a worker process and everything it started, when its ordinary cleanup does not finish.

A child that has started grandchildren can leave a grandchild holding the child's stdout, so
reading the pipe to its end, or waiting for the child, never finishes. `ProcessTree` is attached to
the child right after it is created, before it can start anything, and `terminate()` ends the whole
tree at once. On Windows the child is put in a Job Object and the job is terminated; a child that
cannot be put in one (it is already in a job that forbids nesting) falls back to `taskkill /T`.
On other systems the child leads its own session and its process group is killed.
"""

import ctypes
import os
import signal
import subprocess
import sys
from contextlib import suppress
from ctypes import wintypes

_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001


def _kernel() -> ctypes.WinDLL:
    """kernel32 with the signatures used here; called only on Windows."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    return kernel


class ProcessTree:
    """The tree of one child process. Create it with the child's pid as soon as the child exists."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self._job: int | None = None
        if sys.platform == "win32":
            with suppress(OSError):
                self._job = self._attach_job(pid)

    @staticmethod
    def _attach_job(pid: int) -> int | None:
        kernel = _kernel()
        job = kernel.CreateJobObjectW(None, None)
        if not job:
            return None
        process = kernel.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
        try:
            if process and kernel.AssignProcessToJobObject(job, process):
                return int(job)
        finally:
            if process:
                kernel.CloseHandle(process)
        kernel.CloseHandle(job)
        return None

    def terminate(self) -> None:
        """End the child and its descendants; safe to call again and when they are already gone."""
        if sys.platform == "win32":
            if self._job is not None:
                _kernel().TerminateJobObject(self._job, 1)
            else:
                with suppress(OSError, subprocess.SubprocessError):
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(self.pid)],
                        capture_output=True,
                        timeout=2,
                        check=False,
                    )
        else:
            with suppress(ProcessLookupError, PermissionError):
                os.killpg(self.pid, signal.SIGKILL)

    def close(self) -> None:
        if self._job is not None:
            _kernel().CloseHandle(self._job)
            self._job = None
