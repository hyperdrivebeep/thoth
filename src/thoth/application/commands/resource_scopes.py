"""Source scope reads and explicit grant/revoke commands."""

from pydantic import Field, JsonValue

from thoth.application.services.resource_scope_service import ResourceScopeService
from thoth.domain.base import DomainModel
from thoth.domain.resource_scope import GranteeKind, ResourceScopeTemplate


class ScopeInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)
    resource_ref: str = Field(min_length=1, max_length=260)


class GrantInput(ScopeInput):
    expected_revision: int = Field(ge=1)
    grantee_kind: GranteeKind
    grantee_ref: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=2_000)


class RevokeInput(ScopeInput):
    expected_revision: int = Field(ge=1)
    grant_id: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2_000)


class AssignInput(ScopeInput):
    expected_revision: int = Field(ge=0)
    scope: ResourceScopeTemplate
    reason: str = Field(min_length=1, max_length=2_000)


class ResourceScopeHandlers:
    def __init__(self, scopes: ResourceScopeService) -> None:
        self._scopes = scopes

    async def assign(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = AssignInput.model_validate(value)
        record = self._scopes.assign_legacy(
            request.project_id,
            request.resource_ref,
            expected_revision=request.expected_revision,
            template=request.scope,
            reason=request.reason,
        )
        return {"scope": record.model_dump(mode="json")}

    async def update(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = AssignInput.model_validate(value)
        record = self._scopes.update_scope(
            request.project_id,
            request.resource_ref,
            expected_revision=request.expected_revision,
            template=request.scope,
            reason=request.reason,
        )
        return {"scope": record.model_dump(mode="json")}

    async def read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ScopeInput.model_validate(value)
        record = self._scopes.read_scope(request.project_id, request.resource_ref)
        return {"scope": record.model_dump(mode="json")}

    async def grant(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = GrantInput.model_validate(value)
        record = self._scopes.grant(
            request.project_id,
            request.resource_ref,
            expected_revision=request.expected_revision,
            grantee_kind=request.grantee_kind,
            grantee_ref=request.grantee_ref,
            reason=request.reason,
        )
        return {"scope": record.model_dump(mode="json")}

    async def revoke(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = RevokeInput.model_validate(value)
        record = self._scopes.revoke(
            request.project_id,
            request.resource_ref,
            expected_revision=request.expected_revision,
            grant_id=request.grant_id,
            reason=request.reason,
        )
        return {"scope": record.model_dump(mode="json")}
