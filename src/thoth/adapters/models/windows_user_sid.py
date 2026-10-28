"""The current Windows user's SID, looked up once per process.

Profile files are made private with an ACL naming this user's SID. The SID does not change while the
process lives, but `whoami` is a new process on every call, and on a busy machine starting it can
take longer than its 3 s limit, which showed up as a profile that could not be made private. A
successful lookup is kept for the life of the process; a failed one is never kept, so the next call
asks again. Concurrent callers share one lookup.
"""

from __future__ import annotations

import csv
import io
import subprocess
import threading

WHOAMI_TIMEOUT_SECONDS = 3

_lock = threading.Lock()
_sid: str | None = None


class UserSidUnavailable(Exception):
    """The SID could not be determined; callers turn this into their own typed hold."""


def _lookup() -> str:
    try:
        who = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            capture_output=True,
            text=True,
            encoding="oem",
            errors="replace",
            timeout=WHOAMI_TIMEOUT_SECONDS,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise UserSidUnavailable from exc
    rows = list(csv.reader(io.StringIO(who.stdout)))
    sid = rows[0][1] if who.returncode == 0 and len(rows) == 1 and len(rows[0]) == 2 else ""
    if not sid.startswith("S-1-"):
        raise UserSidUnavailable
    return sid


def current_user_sid() -> str:
    """The SID of the user this process runs as, from `whoami` the first time it succeeds."""
    global _sid
    if _sid is not None:
        return _sid
    with _lock:
        # Callers that waited for the lock find the answer of the one that held it.
        if _sid is None:
            _sid = _lookup()
        return _sid


def forget_user_sid() -> None:
    """Drop the kept SID. For tests that stand in for `whoami`."""
    global _sid
    with _lock:
        _sid = None
