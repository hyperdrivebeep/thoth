from __future__ import annotations

from typing import Protocol

from pydantic import JsonValue

from thoth.domain.projectpack import LoadedProjectPack
from thoth.domain.projectpack_execution import (
    ProjectPackDescriptor,
    ProjectPackExecutionRequest,
)


class ProjectPackLoaderPort(Protocol):
    def list(self) -> tuple[ProjectPackDescriptor, ...]: ...

    def load(self, pack_name: str, *, include_scripted: bool) -> LoadedProjectPack: ...


class ProjectPackExecutionFactoryPort(Protocol):
    async def execute(
        self,
        pack: LoadedProjectPack,
        request: ProjectPackExecutionRequest,
    ) -> dict[str, JsonValue]: ...
