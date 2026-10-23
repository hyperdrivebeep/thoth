from typing import Protocol, runtime_checkable

from thoth.domain.workspace_location import WorkspaceLocation


@runtime_checkable
class WorkspaceLocationReporterPort(Protocol):
    def workspace_location(self) -> WorkspaceLocation | None: ...
