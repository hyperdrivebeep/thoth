"""model/callSettings/read and /update: whether a cut-off model call is retried once."""

from __future__ import annotations

from pydantic import Field, JsonValue

from thoth.application.services.model_call_settings import (
    ModelCallSettingsConflict,
    ModelCallSettingsService,
)
from thoth.domain.auth import current_authenticated_actor
from thoth.domain.base import DomainModel
from thoth.ports.project import ProjectStorePort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


class ModelCallSettingsRead(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)


class ModelCallSettingsUpdate(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)
    # A switch left out keeps its value.
    auto_retry_interrupted_model_call: bool | None = None
    hypothesis_contract_v3: bool | None = None
    expected_digest: str | None = Field(default=None, min_length=64, max_length=64)


class ModelCallSettingsHandlers:
    def __init__(self, *, service: ModelCallSettingsService, projects: ProjectStorePort) -> None:
        self._service, self._projects = service, projects

    def _require_project(self, project_id: str) -> None:
        if self._projects.read(project_id) is None:
            raise RpcApplicationError(RpcErrorCode.PROJECT_NOT_FOUND, "project not found")

    def _value(self, project_id: str) -> dict[str, JsonValue]:
        digest, enabled, contract = self._service.snapshot(project_id)
        return {
            "auto_retry_interrupted_model_call": enabled,
            "hypothesis_contract_v3": contract,
            "settings_digest": digest,
        }

    async def read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ModelCallSettingsRead.model_validate(value)
        self._require_project(request.project_id)
        return self._value(request.project_id)

    async def update(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ModelCallSettingsUpdate.model_validate(value)
        self._require_project(request.project_id)
        actor = current_authenticated_actor()
        try:
            self._service.save(
                request.project_id,
                request.auto_retry_interrupted_model_call,
                request.expected_digest,
                "human:local-user" if actor is None else actor.actor_id,
                request.hypothesis_contract_v3,
            )
        except ModelCallSettingsConflict as exc:
            raise RpcApplicationError(RpcErrorCode.STALE_CHECKPOINT, str(exc)) from exc
        return self._value(request.project_id)
