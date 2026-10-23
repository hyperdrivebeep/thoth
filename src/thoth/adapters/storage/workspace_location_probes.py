"""Read-only, injectable location signals. No sync clients are started or configured."""

from __future__ import annotations

import ctypes
import json
import os
import re
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Literal, cast

from thoth.adapters.storage.workspace_location_registry import registered_sync_roots
from thoth.domain.workspace_identity import windows_path_key

CloudState = Literal["SYNC", "NOT_UNDER_SYNC_ROOT", "UNKNOWN"]
DriveType = Literal["FIXED", "REMOTE", "REMOVABLE", "UNKNOWN"]


@dataclass(frozen=True)
class LocationSignals:
    path: str
    platform: str
    drive_type: DriveType = "UNKNOWN"
    cloud: CloudState = "UNKNOWN"
    cloud_attributes: bool = False
    reparse: bool = False
    known_roots: tuple[str, ...] = ()
    candidate_roots: tuple[str, ...] = ()
    mountinfo: str = ""
    errors: tuple[str, ...] = ()


def cloud_status(hresult: int) -> CloudState:
    value = hresult & 0xFFFFFFFF
    if value == 0:
        return "SYNC"
    # CfGetSyncRootInfoByPath-specific negative signals; other errors remain unknown.
    if value in {0x80070186, 0x80070001}:  # NOT_UNDER_SYNC_ROOT / this API's INVALID_FUNCTION
        return "NOT_UNDER_SYNC_ROOT"
    return "UNKNOWN"


def windows_drive_kind(root: str) -> DriveType:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    query = kernel.GetDriveTypeW
    query.argtypes, query.restype = [ctypes.c_wchar_p], ctypes.c_uint
    kinds: dict[int, DriveType] = {2: "REMOVABLE", 3: "FIXED", 4: "REMOTE"}
    return kinds.get(query(root), "UNKNOWN")


def windows_signals(ancestor: Path) -> tuple[DriveType, CloudState, bool, bool, tuple[str, ...]]:
    errors: list[str] = []
    kind: DriveType = "UNKNOWN"
    cloud: CloudState = "UNKNOWN"
    attributes = 0
    try:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kind = windows_drive_kind(ancestor.anchor)
        if kind == "REMOTE":
            return kind, cloud, False, False, ()
        get_attributes = kernel.GetFileAttributesW
        get_attributes.argtypes, get_attributes.restype = [ctypes.c_wchar_p], ctypes.c_uint32
        attributes = get_attributes(str(ancestor))
        if attributes == 0xFFFFFFFF:
            errors.append("FILE_ATTRIBUTES_UNAVAILABLE")
            attributes = 0
    except (AttributeError, OSError):
        errors.append("DRIVE_PROBE_UNAVAILABLE")
    try:
        library = ctypes.WinDLL("cldapi", use_last_error=True)
        query = library.CfGetSyncRootInfoByPath
        query.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        query.restype = ctypes.c_long
        info = ctypes.c_longlong()
        result = query(str(ancestor), 0, ctypes.byref(info), ctypes.sizeof(info), None)
        cloud = cloud_status(result)
        if cloud == "UNKNOWN":
            errors.append(f"CLOUD_QUERY_FAILED_{result & 0xFFFFFFFF:08X}")
    except (AttributeError, OSError):
        errors.append("CLOUD_API_UNAVAILABLE")
    return (
        kind,
        cloud,
        bool(attributes & (0x1000 | 0x80000 | 0x400000)),
        bool(attributes & 0x400),
        tuple(errors),
    )


def bounded_text(path: Path) -> str | None:
    try:
        with path.open("rb") as stream:
            raw = stream.read(1_048_577)
    except FileNotFoundError:
        return None
    if len(raw) > 1_048_576:
        raise ValueError("LOCATION_METADATA_TOO_LARGE")
    return raw.decode("utf-8")


def provider_roots(
    environ: Mapping[str, str], reader: Callable[[Path], str | None] = bounded_text
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    roots = [
        value
        for key, value in environ.items()
        if key.casefold() in {"onedrive", "onedriveconsumer", "onedrivecommercial"}
        and value.strip()
    ]
    errors: list[str] = []
    for variable in ("APPDATA", "LOCALAPPDATA"):
        if not environ.get(variable):
            continue
        try:
            text = reader(Path(environ[variable]) / "Dropbox" / "info.json")
            data = None if text is None else json.loads(text)
            if data is None:
                continue
            if not isinstance(data, dict):
                raise ValueError("invalid Dropbox metadata")
            for account in cast(dict[str, object], data).values():
                if isinstance(account, dict):
                    path = cast(dict[str, object], account).get("path")
                    if isinstance(path, str) and path:
                        roots.append(path)
        except (OSError, ValueError, UnicodeError):
            errors.append("SYNC_METADATA_UNAVAILABLE")
    return tuple(dict.fromkeys(roots)), tuple(sorted(set(errors)))


def drivefs_candidates(environ: Mapping[str, str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if sys.platform != "win32":
        return (), ()
    import winreg

    paths: list[str] = []
    errors: list[str] = []
    values = {k.casefold(): v for k, v in environ.items()}
    for hive, key in (
        (winreg.HKEY_CURRENT_USER, r"Software\Google\DriveFS"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Google\DriveFS"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Policies\Google\DriveFS"),
    ):
        try:
            with winreg.OpenKey(hive, key) as opened:
                value, _kind = winreg.QueryValueEx(opened, "DefaultMountPoint")
            if isinstance(value, str):
                value = re.sub(
                    r"%([^%]+)%", lambda match: values.get(match[1].casefold(), match[0]), value
                )
                if re.fullmatch(r"[A-Za-z]:?", value):
                    value = value[0] + ":\\"
                if "%" in value:
                    errors.append("SYNC_ROOT_UNRESOLVED")
                elif value:
                    paths.append(value)
        except FileNotFoundError:
            continue
        except OSError:
            errors.append("DRIVEFS_SETTINGS_UNAVAILABLE")
    return tuple(dict.fromkeys(paths)), tuple(sorted(set(errors)))


def probe_workspace(
    workspace: Path,
    *,
    platform: str | None = None,
    environ: Mapping[str, str] | None = None,
    resolve: Callable[[Path], Path] = lambda path: path.expanduser().resolve(),
    reader: Callable[[Path], str | None] = bounded_text,
    drive: Callable[[str], DriveType] = windows_drive_kind,
    windows: Callable[
        [Path], tuple[DriveType, CloudState, bool, bool, tuple[str, ...]]
    ] = windows_signals,
    drivefs: Callable[
        [Mapping[str, str]], tuple[tuple[str, ...], tuple[str, ...]]
    ] = drivefs_candidates,
    registered: Callable[[], tuple[tuple[str, ...], tuple[str, ...]]] = registered_sync_roots,
) -> LocationSignals:
    system, env = platform or sys.platform, os.environ if environ is None else environ
    raw = str(workspace)
    # Lexical network paths do not need a potentially blocking network resolve/stat.
    if system == "win32" and windows_path_key(raw).startswith("\\\\"):
        return LocationSignals(raw, system)
    if system == "win32":
        try:
            anchor = PureWindowsPath(str(workspace.expanduser().absolute())).anchor
            if anchor and drive(anchor) == "REMOTE":
                return LocationSignals(str(workspace.absolute()), system, drive_type="REMOTE")
        except (AttributeError, OSError):
            pass
    try:
        canonical = resolve(workspace)
        if system == "win32" and windows_path_key(str(canonical)).startswith("\\\\"):
            return LocationSignals(str(canonical), system)
        ancestor = canonical
        while True:
            try:
                ancestor.stat()
                break
            except FileNotFoundError:
                if ancestor == ancestor.parent:
                    raise OSError("no existing ancestor") from None
                ancestor = ancestor.parent
    except (OSError, RuntimeError, ValueError):
        return LocationSignals(str(workspace.absolute()), system, errors=("PATH_PROBE_FAILED",))
    if system == "win32":
        roots, metadata_errors = provider_roots(env, reader)
        registered_roots, registry_errors = registered()
        roots = (*roots, *registered_roots)
        candidates, candidate_errors = drivefs(env)
        normalized_roots: list[str] = []
        normalized_candidates: list[str] = []
        errors = [*metadata_errors, *candidate_errors, *registry_errors]
        environment = {key.casefold(): value for key, value in env.items()}
        for source, target in ((roots, normalized_roots), (candidates, normalized_candidates)):
            for root in source:
                try:
                    root = re.sub(
                        r"%([^%]+)%",
                        lambda match: environment.get(match[1].casefold(), match[0]),
                        root,
                    )
                    if "%" in root:
                        raise ValueError("sync root environment is unresolved")
                    if not PureWindowsPath(root).is_absolute():
                        raise ValueError("sync root is not absolute")
                    target.append(str(resolve(Path(root))))
                except (OSError, RuntimeError, ValueError):
                    errors.append("SYNC_ROOT_UNRESOLVED")
        kind, cloud, attributes, reparse, failures = windows(ancestor)
        return LocationSignals(
            str(canonical),
            system,
            kind,
            cloud,
            attributes,
            reparse,
            tuple(normalized_roots),
            tuple(normalized_candidates),
            errors=tuple(sorted(set((*errors, *failures)))),
        )
    if system.startswith("linux"):
        try:
            mounts = reader(Path("/proc/self/mountinfo")) or ""
            return LocationSignals(str(canonical), system, mountinfo=mounts)
        except (OSError, ValueError, UnicodeError):
            return LocationSignals(str(canonical), system, errors=("MOUNTINFO_UNAVAILABLE",))
    return LocationSignals(str(canonical), system, errors=("PLATFORM_PROBE_UNAVAILABLE",))
