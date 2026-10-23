"""Read registered sync roots, without writing registry values or starting providers."""

from __future__ import annotations

from typing import Literal, Protocol

Hive = Literal["HKCU", "HKLM"]


class SyncRootRegistry(Protocol):
    def subkeys(self, hive: Hive, path: str) -> tuple[str, ...]: ...
    def value(self, hive: Hive, path: str, name: str) -> object: ...
    def values(self, hive: Hive, path: str) -> tuple[object, ...]: ...


class WindowsSyncRootRegistry:
    def subkeys(self, hive: Hive, path: str) -> tuple[str, ...]:
        import winreg

        root = winreg.HKEY_CURRENT_USER if hive == "HKCU" else winreg.HKEY_LOCAL_MACHINE
        found: list[str] = []
        with winreg.OpenKey(root, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            for index in range(1024):
                try:
                    found.append(winreg.EnumKey(key, index))
                except OSError as error:
                    if error.winerror == 259:
                        return tuple(found)
                    raise
        raise ValueError("sync root registry enumeration exceeded its bound")

    def value(self, hive: Hive, path: str, name: str) -> object:
        import winreg

        root = winreg.HKEY_CURRENT_USER if hive == "HKCU" else winreg.HKEY_LOCAL_MACHINE
        with winreg.OpenKey(root, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            value, _kind = winreg.QueryValueEx(key, name)
            return value

    def values(self, hive: Hive, path: str) -> tuple[object, ...]:
        import winreg

        root = winreg.HKEY_CURRENT_USER if hive == "HKCU" else winreg.HKEY_LOCAL_MACHINE
        found: list[object] = []
        with winreg.OpenKey(root, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            for index in range(1024):
                try:
                    _name, value, _kind = winreg.EnumValue(key, index)
                    found.append(value)
                except OSError as error:
                    if error.winerror == 259:
                        return tuple(found)
                    raise
        raise ValueError("sync root registry enumeration exceeded its bound")


def registered_sync_roots(
    registry: SyncRootRegistry | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    reader = registry or WindowsSyncRootRegistry()
    roots: list[str] = []
    errors: list[str] = []
    locations: tuple[tuple[Hive, str, str], ...] = (
        ("HKCU", r"Software\Microsoft\OneDrive\Accounts", "onedrive"),
        (
            "HKLM",
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\SyncRootManager",
            "registered",
        ),
    )
    for hive, base, layout in locations:
        try:
            children = reader.subkeys(hive, base)
        except FileNotFoundError:
            continue
        except (OSError, ValueError, ImportError):
            errors.append("SYNC_REGISTRY_UNAVAILABLE")
            continue
        for child in children:
            try:
                values = (
                    (reader.value(hive, base + "\\" + child, "UserFolder"),)
                    if layout == "onedrive"
                    else reader.values(hive, base + "\\" + child + r"\UserSyncRoots")
                )
                for path in values:
                    if isinstance(path, str) and path:
                        roots.append(path)
                    else:
                        errors.append("SYNC_REGISTRY_ROOT_INVALID")
            except FileNotFoundError:
                # The glob has no configured root at this entry; this is not a denied read.
                continue
            except (OSError, ValueError):
                errors.append("SYNC_REGISTRY_UNAVAILABLE")
    return tuple(dict.fromkeys(roots)), tuple(sorted(set(errors)))
