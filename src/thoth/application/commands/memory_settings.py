"""memory/settings/read and memory/settings/update: the project's memory on/off switch."""

from __future__ import annotations

from pydantic import Field, JsonValue

from thoth.application.services.memory_settings import MemorySettingsConflict, MemorySettingsService
from thoth.domain.auth import current_authenticated_actor
from thoth.domain.base import DomainModel
from thoth.ports.project import ProjectStorePort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


class MemorySettingsRead(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)


class MemorySettingsUpdate(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)
    memory_injection: bool
    expected_digest: str | None = Field(default=None, min_length=64, max_length=64)


class MemorySettingsHandlers:
    def __init__(self, *, service: MemorySettingsService, projects: ProjectStorePort) -> None:
        self._service, self._projects = service, projects

    def _require_project(self, project_id: str) -> None:
        if self._projects.read(project_id) is None:
            raise RpcApplicationError(RpcErrorCode.PROJECT_NOT_FOUND, "project not found")

    def _value(self, project_id: str) -> dict[str, JsonValue]:
        digest, enabled = self._service.read(project_id)
        return {"memory_injection": enabled, "settings_digest": digest}

    async def read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = MemorySettingsRead.model_validate(value)
        self._require_project(request.project_id)
        return self._value(request.project_id)

    async def update(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = MemorySettingsUpdate.model_validate(value)
        self._require_project(request.project_id)
        actor = current_authenticated_actor()
        try:
            self._service.save(
                request.project_id,
                request.memory_injection,
                request.expected_digest,
                "human:local-user" if actor is None else actor.actor_id,
            )
        except MemorySettingsConflict as exc:
            raise RpcApplicationError(RpcErrorCode.STALE_CHECKPOINT, str(exc)) from exc
        return self._value(request.project_id)
