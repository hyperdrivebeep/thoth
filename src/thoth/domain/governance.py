from __future__ import annotations

from pydantic import AwareDatetime, Field, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.ids import ProjectId, Sha256


class Organization(DomainModel):
    organization_id: str
    name: str
    kind: str
    created_at: AwareDatetime
    schema_version: str = "1.0.0"


class RoleAssignment(DomainModel):
    role_assignment_id: str
    project_id: ProjectId
    actor_id: str
    organization_id: str | None = None
    role: str
    scope: str = "PROJECT"
    authority_tags: tuple[str, ...] = ()
    state: str = "ACTIVE"
    created_at: AwareDatetime
    revoked_at: AwareDatetime | None = None
    schema_version: str = "1.0.0"

    @model_validator(mode="after")
    def revoked_has_timestamp(self) -> RoleAssignment:
        if self.state == "REVOKED" and self.revoked_at is None:
            raise ValueError("revoked role assignment requires revoked_at")
        return self


class Workstream(DomainModel):
    workstream_id: str
    project_id: ProjectId
    name: str
    parent_workstream_id: str | None = None
    owner_role_assignment_id: str | None = None
    state: str = "ACTIVE"
    revision: int = Field(default=0, ge=0)
    schema_version: str = "1.0.0"


class ProjectPolicy(DomainModel):
    policy_id: str
    project_id: ProjectId
    version: int = Field(ge=1)
    payload: dict[str, object]
    policy_digest: Sha256
    created_at: AwareDatetime
    schema_version: str = "1.0.0"


class ProjectReference(DomainModel):
    reference_id: str
    project_id: ProjectId
    origin_project_id: ProjectId
    origin_revision: str
    rights_status: str
    scope: str
    authority_status: str = "EXTERNAL_REFERENCE"
    created_at: AwareDatetime
    schema_version: str = "1.0.0"


class SourceBinding(DomainModel):
    binding_id: str
    project_id: ProjectId
    artifact_id: str
    capability: str = "READ"
    state: str = "ACTIVE"
    created_at: AwareDatetime
    updated_at: AwareDatetime
    schema_version: str = "1.0.0"

    @model_validator(mode="after")
    def supported_state(self) -> SourceBinding:
        if self.state not in {"ACTIVE", "DETACHED", "REVOKED"}:
            raise ValueError("unsupported source binding state")
        return self
