from __future__ import annotations

from pydantic import Field, JsonValue

from thoth.domain.base import DomainModel
from thoth.domain.projectpack_execution import ProjectPackExecutionRequest
from thoth.ports.projectpack import (
    ProjectPackExecutionFactoryPort,
    ProjectPackLoaderPort,
)
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


class ProjectPackListInput(DomainModel):
    project_id: str = "system:projectpacks"


class ProjectPackRunInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)
    pack_name: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9._-]+$")
    problem: str | None = Field(default=None, max_length=20_000)
    scripted: bool = False
    provider: str = Field(default="default", pattern=r"^[A-Za-z0-9._-]+$")
    model: str | None = Field(default=None, max_length=160)


class ProjectPackCommandHandlers:
    def __init__(
        self,
        *,
        loader: ProjectPackLoaderPort,
        executions: ProjectPackExecutionFactoryPort,
    ) -> None:
        self._loader = loader
        self._executions = executions

    async def list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        ProjectPackListInput.model_validate(value)
        return {
            "packs": [item.model_dump(mode="json") for item in self._loader.list()]
        }

    async def run(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ProjectPackRunInput.model_validate(value)
        try:
            pack = self._loader.load(request.pack_name, include_scripted=request.scripted)
        except ValueError as exc:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED,
                "ProjectPack failed validation",
                data={"pack_name": request.pack_name},
            ) from exc
        if pack.project.project_id != request.project_id:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED,
                "request project_id does not match ProjectPack",
            )
        if request.problem:
            pack = pack.model_copy(
                update={"scenario": pack.scenario.model_copy(update={"problem": request.problem})}
            )
        try:
            result = await self._executions.execute(
                pack,
                ProjectPackExecutionRequest(
                    provider=request.provider,
                    model=request.model,
                    scripted=request.scripted,
                ),
            )
        except ValueError as exc:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED,
                "ProjectPack execution was rejected",
                data={"pack_name": request.pack_name},
            ) from exc
        return {str(key): child for key, child in result.items()}
