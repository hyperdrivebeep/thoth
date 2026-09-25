from __future__ import annotations

from typing import Protocol

from thoth.domain.workspace_setup import WorkspaceSetupState


class WorkspaceSetupPort(Protocol):
    def read(self) -> WorkspaceSetupState: ...

    def write(self, state: WorkspaceSetupState) -> WorkspaceSetupState: ...
