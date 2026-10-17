"""Input contracts for revisions_full; handlers and state transitions remain in their owner."""

from __future__ import annotations

from pydantic import Field, JsonValue

from thoth.domain.base import DomainModel
from thoth.domain.enums import EntityType


class ProjectInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)


class RevisionListInput(ProjectInput):
    aggregate_id: str | None = Field(default=None, max_length=160)
    aggregate_type: EntityType | None = None
    commit_state: str | None = Field(default=None, max_length=80)
    freshness: str | None = Field(default=None, max_length=80)


class RevisionReadInput(ProjectInput):
    revision_digest: str = Field(min_length=64, max_length=64)


class ContentReadInput(ProjectInput):
    content_digest: str = Field(min_length=64, max_length=64)


class DiffReadInput(ProjectInput):
    from_revision_digest: str = Field(min_length=64, max_length=64)
    to_revision_digest: str = Field(min_length=64, max_length=64)
    diff_profile_ref: str | None = Field(default=None, max_length=160)


class GraphReadInput(ProjectInput):
    aggregate_id: str | None = Field(default=None, max_length=160)
    revision_digest: str | None = Field(default=None, min_length=64, max_length=64)
    depth: int = Field(default=20, ge=1, le=1_000)
    relation_types: tuple[str, ...] = ()


class HeadReadInput(ProjectInput):
    aggregate_ids: tuple[str, ...] = ()


class RecordReadInput(ProjectInput):
    record_id: str = Field(min_length=1, max_length=160)


class ConflictListInput(ProjectInput):
    aggregate_id: str | None = Field(default=None, max_length=160)
    state: str | None = Field(default=None, max_length=80)


class RestorePreviewInput(ProjectInput):
    aggregate_id: str = Field(min_length=1, max_length=160)
    target_revision_digest: str = Field(min_length=64, max_length=64)
    current_head_digest: str = Field(min_length=64, max_length=64)


class BaselineListInput(ProjectInput):
    lifecycle: str | None = Field(default=None, max_length=80)


class BaselineReadInput(ProjectInput):
    baseline_set_digest: str = Field(min_length=64, max_length=64)


class AuditReadInput(ProjectInput):
    revision_digest: str | None = Field(default=None, min_length=64, max_length=64)
    change_set_id: str | None = Field(default=None, max_length=160)
    aggregate_id: str | None = Field(default=None, max_length=160)


class ProposeInput(ProjectInput):
    aggregate_id: str = Field(min_length=1, max_length=160)
    aggregate_type: EntityType
    parent_revision_digests: tuple[str, ...]
    candidate_content: dict[str, JsonValue]
    reason: str = Field(min_length=1, max_length=5_000)
    evidence_refs: tuple[str, ...]
    actor_or_agent_ref: str = Field(min_length=1, max_length=160)
    expected_head_digest: str | None = Field(default=None, min_length=64, max_length=64)
    candidate_schema_version: str = Field(default="1.0.0", min_length=1, max_length=80)


class ChangeSetCreateInput(ProjectInput):
    expected_head_set: dict[str, str]
    candidate_revision_digests: tuple[str, ...]
    transition_reason: str = Field(min_length=1, max_length=5_000)
    impact_policy_ref: str = Field(min_length=1, max_length=160)


class ChangeSetValidateInput(RecordReadInput):
    expected_change_set_revision: int = Field(ge=1)


class ChangeSetCommitInput(RecordReadInput):
    validation_bundle_digest: str = Field(min_length=64, max_length=64)
    expected_head_set_digest: str = Field(min_length=64, max_length=64)


class BranchCreateInput(ProjectInput):
    aggregate_id: str = Field(min_length=1, max_length=160)
    from_revision_digest: str = Field(min_length=64, max_length=64)
    purpose: str = Field(min_length=1, max_length=2_000)
    actor_or_agent_ref: str = Field(min_length=1, max_length=160)


class MergeProposeInput(ProjectInput):
    aggregate_id: str = Field(min_length=1, max_length=160)
    parent_revision_digests: tuple[str, ...] = Field(min_length=2)
    candidate_content: dict[str, JsonValue] | None = None
    merge_policy_ref: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=5_000)
    evidence_refs: tuple[str, ...]


class MergeResolveInput(ProjectInput):
    merge_proposal_id: str = Field(min_length=1, max_length=160)
    resolved_content: dict[str, JsonValue]
    resolution_map: dict[str, JsonValue]
    evidence_refs: tuple[str, ...]
    actor_or_agent_ref: str = Field(min_length=1, max_length=160)
    expected_conflict_revision: int = Field(ge=1)


class RestoreProposeInput(ProjectInput):
    aggregate_id: str = Field(min_length=1, max_length=160)
    current_head_digest: str = Field(min_length=64, max_length=64)
    target_revision_digest: str = Field(min_length=64, max_length=64)
    reason: str = Field(min_length=1, max_length=5_000)
    evidence_refs: tuple[str, ...]
    actor_or_agent_ref: str = Field(min_length=1, max_length=160)


class RecomputeRequestInput(ProjectInput):
    change_set_id: str = Field(min_length=1, max_length=160)
    projection_refs: tuple[str, ...]
    recompute_policy_ref: str = Field(min_length=1, max_length=160)


class BaselinePrepareInput(ProjectInput):
    purpose: str = Field(min_length=1, max_length=2_000)
    scoped_head_map: dict[str, str]
    policy_version: str = Field(min_length=1, max_length=160)
    evidence_refs: tuple[str, ...]


class BaselineDecideInput(ProjectInput):
    baseline_candidate_id: str = Field(min_length=1, max_length=160)
    decision: str = Field(pattern=r"^(APPROVE|REJECT)$")
    actor_ref: str = Field(min_length=1, max_length=160)
    role_assignment_ref: str = Field(min_length=1, max_length=160)
    approved_digest: str = Field(min_length=64, max_length=64)
    reason: str | None = Field(default=None, max_length=2_000)
