"""Conservative command grammar; a checkpoint never authorizes adjacent shell actions."""

from __future__ import annotations

import re
import shlex


def checkpoint_command(text: str) -> tuple[str | None, str | None, str | None]:
    if not re.search(r"\bgit(?:\.exe)?\b[^\r\n]*(?:\bcommit\b|\bpush\b)", text, re.I):
        return None, None, None
    # Reject expansion, multiple commands, redirection and shell interpolation even in quotes.
    if any(char in text for char in "\r\n;&|<>`$"):
        return "invalid", None, None
    try:
        tokens = shlex.split(text, posix=True)
    except ValueError:
        return "invalid", None, None
    if len(tokens) < 2 or tokens[0] not in {"git", "git.exe"}:
        return "invalid", None, None
    if tokens[1] == "commit":
        # No -a, pathspec, alternate index, amend, hooks/config overrides or option abbreviations.
        if len(tokens) == 4 and tokens[2] in {"-m", "--message"} and tokens[3].strip():
            return "commit", None, None
        return "invalid", None, None
    if tokens[1] != "push":
        return "invalid", None, None
    args = tokens[2:]
    if args and args[0] in {"-u", "--set-upstream"}:
        args = args[1:]
    if len(args) != 2:
        return "invalid", None, None
    remote, branch = args
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", remote):
        return "invalid", None, None
    if (
        not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_./-]*", branch)
        or ".." in branch
        or branch.endswith(("/", ".lock", "."))
    ):
        return "invalid", None, None
    return "push", remote, branch
