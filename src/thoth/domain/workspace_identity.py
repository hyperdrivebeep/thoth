"""Pure identity of an already resolved workspace path."""

import ntpath
from hashlib import sha256


def workspace_identity(path: str, *, windows: bool) -> str:
    normalized = path.casefold() if windows else path
    return "workspace:" + sha256(normalized.encode("utf-8")).hexdigest()[:32]


def windows_path_key(path: str) -> str:
    value = path.replace("/", "\\")
    if value.casefold().startswith("\\\\?\\unc\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return ntpath.normpath(value).casefold()
