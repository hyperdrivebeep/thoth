"""Files only the current user may read: the permission step and a whole-file JSON write.

The sign-in profiles and the API key file keep secrets in the workspace. They share this module so a
secret gets the same protection wherever it is stored: on Windows an ACL that gives the current user
full control, drops what the folder passed down and drops entries for broad groups (a file mode does
nothing there), elsewhere mode 0600/0700.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import cast

from thoth.adapters.models.windows_user_sid import UserSidUnavailable, current_user_sid

# Groups that stand for many accounts. "/inheritance:r" only removes entries marked as inherited,
# so an entry for one of these that sits on the file itself would stay and keep the secret readable
# by other users; they are removed by name. Entries for SYSTEM and Administrators are left: backup
# and recovery tools work through them, and an administrator can read the file in any case.
BROAD_GROUP_SIDS = (
    "S-1-1-0",  # Everyone
    "S-1-5-11",  # Authenticated Users
    "S-1-5-32-545",  # BUILTIN Users
    "S-1-5-32-546",  # BUILTIN Guests
    "S-1-5-4",  # INTERACTIVE
    "S-1-5-14",  # REMOTE INTERACTIVE LOGON
    "S-1-5-2",  # NETWORK
    "S-1-5-7",  # ANONYMOUS LOGON
    "S-1-5-15",  # This Organization
    "S-1-5-113",  # Local account
    "S-1-2-0",  # LOCAL
    "S-1-2-1",  # CONSOLE LOGON
)


class PrivatePathError(OSError):
    """The path could not be restricted to the current user."""


class UnreadableJsonError(Exception):
    """A file that is about to be replaced exists but does not hold a JSON object."""


def read_for_replace(path: Path) -> dict[str, object]:
    """What the file holds, for a write that will replace it.

    A file that is not there yet is empty. A file that is there and cannot be read raises: treating
    it as empty would write the new entry alone and drop everything the file held.
    """
    if not path.exists():
        return {}
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UnreadableJsonError(str(path.name)) from exc
    if not isinstance(raw, dict):
        raise UnreadableJsonError(str(path.name))
    return {
        key: value for key, value in cast(dict[object, object], raw).items() if isinstance(key, str)
    }


def make_private(path: Path, *, directory: bool = False) -> None:
    """Give the current user full control and take the path away from other ordinary accounts.

    On Windows: entries passed down by the folder go, the current user gets full control, and
    entries for broad groups (BROAD_GROUP_SIDS) set on the path itself go. Elsewhere: mode 0600 or
    0700.
    """
    if os.name == "nt":
        try:
            sid = current_user_sid()
        except UserSidUnavailable as exc:
            raise PrivatePathError("current user SID is unavailable") from exc
        try:
            result = subprocess.run(
                [
                    "icacls",
                    str(path),
                    "/inheritance:r",
                    "/grant:r",
                    f"*{sid}:(OI)(CI)F" if directory else f"*{sid}:F",
                    "/remove:g",
                    *(f"*{group}" for group in BROAD_GROUP_SIDS),
                ],
                capture_output=True,
                text=True,
                encoding="oem",
                errors="replace",
                timeout=5,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise PrivatePathError("icacls could not run") from exc
        if result.returncode != 0:
            raise PrivatePathError("icacls refused the permission change")
    else:
        try:
            os.chmod(path, 0o700 if directory else 0o600)
        except OSError as exc:
            raise PrivatePathError("chmod failed") from exc


def replace_json(path: Path, payload: dict[str, object], *, private: bool) -> None:
    """Write the file as a whole.

    The new content goes to a temporary file in the same folder, is flushed to disk and then takes
    the place of the old file in one step. A crash or a full disk in the middle leaves the old file
    as it was, never half of the new one. With private=True the file is restricted to the current
    user before it gets its final name. Raises OSError (PrivatePathError included) on failure.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}-", delete=False
        ) as stream:
            name = stream.name
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temp = Path(name)
        if private:
            make_private(temp)
        os.replace(temp, path)
        if private:
            make_private(path)
    finally:
        if name is not None:
            Path(name).unlink(missing_ok=True)
