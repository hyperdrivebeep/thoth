"""Local process identity; unknown liveness never authorizes early lease takeover."""

import ctypes
import os
from pathlib import Path
from typing import Any, ClassVar, cast
from uuid import uuid4


def _process_marker(pid: int) -> str | None:
    if os.name != "nt":
        try:
            return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
        except FileNotFoundError:
            return "DEAD"
        except OSError:
            return None
    from ctypes import wintypes

    library = cast(Any, ctypes.WinDLL)("kernel32", use_last_error=True)
    library.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    library.OpenProcess.restype = wintypes.HANDLE
    handle = library.OpenProcess(0x1000, False, pid)
    if not handle:
        return "DEAD" if ctypes.get_last_error() in (87, 1168) else None
    try:
        code = wintypes.DWORD()
        library.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        if library.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value != 259:
            return "DEAD"
        stamps = [wintypes.FILETIME() for _ in range(4)]
        library.GetProcessTimes.argtypes = (
            wintypes.HANDLE,
            *([ctypes.POINTER(wintypes.FILETIME)] * 4),
        )
        if not library.GetProcessTimes(handle, *(ctypes.byref(value) for value in stamps)):
            return None
        return str((stamps[0].dwHighDateTime << 32) | stamps[0].dwLowDateTime)
    finally:
        library.CloseHandle.argtypes = (wintypes.HANDLE,)
        library.CloseHandle(handle)


class LocalWorkerIdentity:
    _active: ClassVar[set[str]] = set()

    def __init__(self) -> None:
        self.worker_id = "worker:" + str(uuid4())
        self.process_id = os.getpid()
        self.process_marker = _process_marker(self.process_id) or "UNKNOWN"
        self._active.add(self.worker_id)

    def is_alive(self, process_id: int, process_marker: str, worker_id: str) -> bool | None:
        if process_id == self.process_id and process_marker == self.process_marker:
            return worker_id in self._active
        current = _process_marker(process_id)
        if current is None or process_marker == "UNKNOWN":
            return None
        return current != "DEAD" and current == process_marker

    def close(self) -> None:
        self._active.discard(self.worker_id)
