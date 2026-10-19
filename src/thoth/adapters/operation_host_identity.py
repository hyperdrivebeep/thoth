"""Best-effort host/boot equality token; not an authentication or liveness proof."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import sys
import threading
from pathlib import Path
from uuid import UUID

from thoth.domain.deployment_mode import DeploymentMode

_KEY = b"thoth.operation-owner.host.v1"
_cache: tuple[int, str | None] | None = None
_lock = threading.Lock()


def _guid(value: str) -> str:
    if not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", value):
        raise ValueError("HOST_IDENTITY_FORMAT")
    parsed = UUID(value)
    if parsed.int == 0:
        raise ValueError("HOST_IDENTITY_FORMAT")
    return parsed.hex


def _windows_identity() -> tuple[str, str]:
    import winreg

    access = winreg.KEY_READ | winreg.KEY_WOW64_64KEY
    boot_path = (
        r"SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management\PrefetchParameters"
    )

    def boot() -> int:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, boot_path, 0, access) as key:
            value, kind = winreg.QueryValueEx(key, "BootId")
        if kind != winreg.REG_DWORD or type(value) is not int or not 0 <= value <= 2**32 - 1:
            raise ValueError("HOST_IDENTITY_FORMAT")
        return value

    before = boot()
    with winreg.OpenKey(
        winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0, access
    ) as key:
        machine, kind = winreg.QueryValueEx(key, "MachineGuid")
    after = boot()
    if before != after or kind != winreg.REG_SZ or not isinstance(machine, str):
        raise ValueError("HOST_IDENTITY_UNAVAILABLE")
    return _guid(machine), "prefetch-bootid:" + str(before)


def _bounded_text(path: str) -> str:
    with Path(path).open("rb") as source:
        raw = source.read(129)
    if len(raw) > 128:
        raise ValueError("HOST_IDENTITY_FORMAT")
    return raw.decode("ascii").strip()


def _linux_identity() -> tuple[str, str]:
    machine = _bounded_text("/etc/machine-id")
    if not re.fullmatch(r"[0-9a-fA-F]{32}", machine) or int(machine, 16) == 0:
        raise ValueError("HOST_IDENTITY_FORMAT")
    return machine.lower(), _guid(_bounded_text("/proc/sys/kernel/random/boot_id"))


def _read_token() -> str | None:
    try:
        if sys.platform == "win32":
            machine, boot = _windows_identity()
        elif sys.platform == "linux":
            machine, boot = _linux_identity()
        else:
            return None
        payload = (sys.platform + "\0" + machine + "\0" + boot).encode("utf-8")
        return hmac.new(_KEY, payload, hashlib.sha256).hexdigest()
    except (OSError, ValueError, TypeError, ImportError):
        return None


def operation_host_token(mode: DeploymentMode) -> str | None:
    """Read once per LOCAL process, caching failure too; HOSTED performs no lookup."""
    if mode is not DeploymentMode.LOCAL:
        return None
    global _cache
    with _lock:
        pid = os.getpid()
        if _cache is None or _cache[0] != pid:
            _cache = (pid, _read_token())
        return _cache[1]


def _after_fork() -> None:
    global _cache, _lock
    _cache = None
    _lock = threading.Lock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)
