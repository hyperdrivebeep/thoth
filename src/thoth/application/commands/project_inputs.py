"""Project RPC input contracts, shared by project management and restore handlers."""

from __future__ import annotations

from pydantic import AwareDatetime, Field, JsonValue

from thoth.domain.base import DomainModel


class ProjectCreateInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=10_000)
    cutoff_at: AwareDatetime
    overlay: str = Field(default="default", min_length=1, max_length=160)
    policy_binding_ref: str = Field(default="policy:default", min_length=1, max_length=160)


class ProjectReadInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)


class ProjectListInput(DomainModel):
    project_id: str = Field(default="system:projects", min_length=1, max_length=160)
    include_archived: bool = False


class ProjectExpectedRevisionInput(ProjectReadInput):
    expected_revision: int = Field(ge=0)


class ProjectMetadataUpdateInput(ProjectExpectedRevisionInput):
    name: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=10_000)


class ProjectOverlayUpdateInput(ProjectExpectedRevisionInput):
    overlay: str = Field(min_length=1, max_length=160)


class ProjectPolicyUpdateInput(ProjectExpectedRevisionInput):
    payload: dict[str, JsonValue]


class ProjectRoleAssignInput(ProjectExpectedRevisionInput):
    actor_id: str = Field(min_length=1, max_length=160)
    organization_id: str | None = Field(default=None, max_length=160)
    role: str = Field(min_length=1, max_length=160)
    scope: str = Field(default="PROJECT", min_length=1, max_length=260)
    authority_tags: tuple[str, ...] = ()


class ProjectRoleRevokeInput(ProjectExpectedRevisionInput):
    role_assignment_id: str = Field(min_length=1, max_length=160)


class ProjectReferenceImportInput(ProjectExpectedRevisionInput):
    origin_project_id: str = Field(min_length=1, max_length=160)
    origin_revision: str = Field(min_length=1, max_length=160)
    rights_status: str = Field(min_length=1, max_length=80)
    scope: str = Field(min_length=1, max_length=260)


class ProjectCutoffImpactInput(ProjectReadInput):
    proposed_cutoff_at: AwareDatetime


class ProjectCutoffUpdateInput(ProjectExpectedRevisionInput):
    cutoff_at: AwareDatetime
    expected_impact_digest: str = Field(min_length=64, max_length=64)
