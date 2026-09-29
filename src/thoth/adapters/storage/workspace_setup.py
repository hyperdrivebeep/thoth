from __future__ import annotations

import json
import os
from pathlib import Path
from typing import cast
from uuid import uuid4

from pydantic import ValidationError

from thoth.domain.workspace_setup import WorkspaceSetupState
from thoth.ports.workspace_setup import WorkspaceSetupPort, WorkspaceSetupUnavailable

_FILE = "workspace-setup.json"


def _path(root: Path | None = None) -> Path:
    base = root or Path(os.environ.get("THOTH_WORKSPACE", ".thoth"))
    return base.resolve() / _FILE


def read_setup(root: Path | None = None) -> WorkspaceSetupState:
    path = _path(root)
    if path.is_symlink():
        return WorkspaceSetupState(storage_status="UNREADABLE")
    try:
        raw_object: object = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return WorkspaceSetupState(storage_status="MISSING")
    except OSError:
        return WorkspaceSetupState(storage_status="UNREADABLE")
    except (ValueError, TypeError):
        return WorkspaceSetupState(storage_status="CORRUPT")
    if not isinstance(raw_object, dict):
        return WorkspaceSetupState(storage_status="CORRUPT")
    raw = cast(dict[str, object], raw_object)
    if (
        type(raw.get("schema_version")) is not int
        or raw.get("schema_version") != 1
        or not {"revision", "internet_consent"}.issubset(raw)
        or "storage_status" in raw
    ):
        return WorkspaceSetupState(storage_status="CORRUPT")
    try:
        state = WorkspaceSetupState.model_validate(raw)
    except ValidationError:
        return WorkspaceSetupState(storage_status="CORRUPT")
    if state.internet_consent == "ALLOWED" and not state.internet_grant_id:
        return WorkspaceSetupState(storage_status="CORRUPT")
    if state.internet_consent != "ALLOWED" and state.internet_grant_id is not None:
        return WorkspaceSetupState(storage_status="CORRUPT")
    return state


def write_setup(state: WorkspaceSetupState, root: Path | None = None) -> WorkspaceSetupState:
    path = _path(root)
    current = read_setup(root)
    if current.storage_status in {"CORRUPT", "UNREADABLE"}:
        raise WorkspaceSetupUnavailable(
            "WORKSPACE_SETUP_CORRUPT"
            if current.storage_status == "CORRUPT"
            else "WORKSPACE_SETUP_UNREADABLE"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    updated = state.model_copy(update={"revision": state.revision + 1, "storage_status": "PRESENT"})
    if updated.internet_consent == "ALLOWED" and not updated.internet_grant_id:
        updated = updated.model_copy(update={"internet_grant_id": f"grant:{uuid4()}"})
    if updated.internet_consent != "ALLOWED":
        updated = updated.model_copy(update={"internet_grant_id": None})
    temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temp.open("x", encoding="utf-8") as stream:
            stream.write(updated.model_dump_json(indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return updated


class FilesystemWorkspaceSetup(WorkspaceSetupPort):
    def __init__(self, root: Path | None = None) -> None:
        self.root = root

    def read(self) -> WorkspaceSetupState:
        return read_setup(self.root)

    def write(self, state: WorkspaceSetupState) -> WorkspaceSetupState:
        return write_setup(state, self.root)
