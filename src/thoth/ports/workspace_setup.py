from __future__ import annotations

from typing import Literal, Protocol

from thoth.domain.workspace_setup import WorkspaceSetupState


class WorkspaceSetupUnavailable(RuntimeError):
    """An existing setup cannot be treated as a new, undecided workspace."""

    def __init__(
        self,
        reason_code: Literal["WORKSPACE_SETUP_CORRUPT", "WORKSPACE_SETUP_UNREADABLE"],
    ) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.setup_status: Literal["CORRUPT", "UNREADABLE"] = (
            "CORRUPT" if reason_code == "WORKSPACE_SETUP_CORRUPT" else "UNREADABLE"
        )


class WorkspaceSetupPort(Protocol):
    def read(self) -> WorkspaceSetupState: ...

    def write(self, state: WorkspaceSetupState) -> WorkspaceSetupState: ...
