"""Read trusted-size/hash files through checked handles, without following import links."""

import ctypes
import hashlib
import os
import stat
import sys
import threading
import time
from contextlib import suppress
from pathlib import Path

from thoth.domain.model_asset import AssetFile, ModelAssetError


def check_time(deadline: float | None, cancelled: threading.Event | None = None) -> None:
    if cancelled is not None and cancelled.is_set():
        raise ModelAssetError("MODEL_ASSET_CANCELLED")
    if deadline is not None and time.monotonic() >= deadline:
        raise ModelAssetError("MODEL_ASSET_TIMEOUT")


def logical_path(path: Path) -> Path:
    text = str(path)
    if os.name == "nt":
        if text.startswith("\\\\?\\UNC\\"):
            text = "\\\\" + text[8:]
        elif text.startswith("\\\\?\\"):
            text = text[4:]
    return Path(os.path.abspath(text))


def native_path(path: Path) -> Path:
    absolute = logical_path(path)
    return Path("\\\\?\\" + str(absolute)) if os.name == "nt" else absolute


def local_path(path: Path) -> Path:
    absolute = logical_path(path)
    if "\x00" in str(absolute):
        raise ModelAssetError("MODEL_ASSET_PATH_INVALID")
    if os.name == "nt":
        if absolute.anchor.startswith("\\\\"):
            raise ModelAssetError("MODEL_ASSET_PATH_INVALID")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        if kernel.GetDriveTypeW(ctypes.c_wchar_p(absolute.anchor)) == 4:
            raise ModelAssetError("MODEL_ASSET_PATH_INVALID")
    return absolute


def ensure_directory(path: Path) -> None:
    absolute = local_path(path)
    for part in (*reversed(absolute.parents), absolute):
        try:
            info = native_path(part).lstat()
        except FileNotFoundError:
            with suppress(FileExistsError):
                native_path(part).mkdir(mode=0o700)
            info = native_path(part).lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 0x400
        ):
            raise ModelAssetError("MODEL_ASSET_PATH_INVALID")


def plain_path(path: Path) -> Path:
    absolute = local_path(path)
    for part in (*reversed(absolute.parents), absolute):
        try:
            info = native_path(part).lstat()
        except FileNotFoundError as exc:
            raise ModelAssetError("MODEL_NOT_INSTALLED") from exc
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ModelAssetError("MODEL_ASSET_PATH_INVALID")
    return absolute


def _opened_path(fd: int) -> Path:
    if sys.platform == "win32":
        import msvcrt
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        function = kernel.GetFinalPathNameByHandleW
        function.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        function.restype = wintypes.DWORD
        value = ctypes.create_unicode_buffer(32768)
        size = function(msvcrt.get_osfhandle(fd), value, len(value), 0)
        if size == 0 or size >= len(value):
            raise ModelAssetError("MODEL_ASSET_IO_FAILED")
        text = value.value
        if text.startswith("\\\\?\\UNC\\"):
            text = "\\\\" + text[8:]
        elif text.startswith("\\\\?\\"):
            text = text[4:]
        return Path(text)
    if sys.platform.startswith("linux"):
        return Path(os.readlink(f"/proc/self/fd/{fd}"))
    raise ModelAssetError("MODEL_ASSET_PLATFORM_UNSUPPORTED")


def verified_bytes(
    root: Path,
    spec: AssetFile,
    *,
    deadline: float | None = None,
    cancelled: threading.Event | None = None,
) -> bytes:
    check_time(deadline, cancelled)
    root = plain_path(root)
    target = plain_path(root.joinpath(*spec.path.split("/")))
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOINHERIT", 0)
    try:
        with os.fdopen(os.open(native_path(target), flags), "rb") as stream:
            before = os.fstat(stream.fileno())
            actual = _opened_path(stream.fileno())
            if (
                not actual.is_relative_to(root)
                or actual != target
                or not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
            ):
                raise ModelAssetError("MODEL_ASSET_PATH_INVALID")
            if before.st_size != spec.bytes:
                raise ModelAssetError("MODEL_HASH_MISMATCH")
            digest = hashlib.sha256()
            chunks: list[bytes] = []
            size = 0
            while True:
                check_time(deadline, cancelled)
                chunk = stream.read(min(1024 * 1024, spec.bytes - size + 1))
                if not chunk:
                    break
                size += len(chunk)
                if size > spec.bytes:
                    raise ModelAssetError("MODEL_HASH_MISMATCH")
                digest.update(chunk)
                chunks.append(chunk)
            after = os.fstat(stream.fileno())
            if (
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                or size != spec.bytes
                or digest.hexdigest() != spec.sha256
            ):
                raise ModelAssetError("MODEL_HASH_MISMATCH")
            check_time(deadline, cancelled)
            return b"".join(chunks)
    except FileNotFoundError as exc:
        raise ModelAssetError("MODEL_NOT_INSTALLED") from exc
    except OSError as exc:
        raise ModelAssetError("MODEL_ASSET_IO_FAILED") from exc
