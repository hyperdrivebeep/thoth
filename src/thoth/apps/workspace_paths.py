"""Stable local workspace selection without migrating older workspace data."""

from __future__ import annotations

import hashlib
import os
import sys
from collections.abc import Mapping
from pathlib import Path

from thoth.domain.workspace_identity import workspace_identity


def default_workspace(
    *,
    platform: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    system = sys.platform if platform is None else platform
    values = os.environ if environ is None else environ
    user_home = Path.home() if home is None else home
    if system == "win32":
        local_app_data = values.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else user_home / "AppData" / "Local"
        return (base / "THOTH").expanduser().resolve()
    return (user_home / ".thoth").expanduser().resolve()


def selected_workspace(explicit: Path | None = None) -> Path:
    return (default_workspace() if explicit is None else explicit.expanduser()).resolve()


def workspace_id(root: Path, *, platform: str | None = None) -> str:
    normalized = str(root.expanduser().resolve())
    return workspace_identity(
        normalized, windows=(sys.platform if platform is None else platform) == "win32"
    )


def workspace_session_id(workspace: Path) -> str:
    normalized = os.path.normcase(str(workspace.resolve()))
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
    return f"tui:workspace:{digest}:local"


def legacy_workspace_candidates(cwd: Path) -> tuple[Path, ...]:
    """Report only; never move, open, or delete a legacy workspace."""
    return tuple(
        path for name in (".thoth-local", ".thoth") if (path := (cwd / name).resolve()).is_dir()
    )
