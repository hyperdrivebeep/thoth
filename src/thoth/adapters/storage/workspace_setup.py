from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

from thoth.domain.workspace_setup import WorkspaceSetupState
from thoth.ports.workspace_setup import WorkspaceSetupPort

_FILE = "workspace-setup.json"


def _path(root: Path | None = None) -> Path:
    base = root or Path(os.environ.get("THOTH_WORKSPACE", ".thoth"))
    return base.resolve() / _FILE


def read_setup(root: Path | None = None) -> WorkspaceSetupState:
    path = _path(root)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return WorkspaceSetupState()
    if not isinstance(raw, dict):
        return WorkspaceSetupState()
    return WorkspaceSetupState.model_validate(raw)


def write_setup(state: WorkspaceSetupState, root: Path | None = None) -> WorkspaceSetupState:
    path = _path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    updated = state.model_copy(update={"revision": state.revision + 1})
    if updated.internet_consent == "ALLOWED" and not updated.internet_grant_id:
        updated = updated.model_copy(update={"internet_grant_id": f"grant:{uuid4()}"})
    if updated.internet_consent != "ALLOWED":
        updated = updated.model_copy(update={"internet_grant_id": None})
    temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temp.write_text(updated.model_dump_json(indent=2) + "\n", encoding="utf-8")
    temp.replace(path)
    return updated


class FilesystemWorkspaceSetup(WorkspaceSetupPort):
    def __init__(self, root: Path | None = None) -> None:
        self.root = root

    def read(self) -> WorkspaceSetupState:
        return read_setup(self.root)

    def write(self, state: WorkspaceSetupState) -> WorkspaceSetupState:
        return write_setup(state, self.root)
