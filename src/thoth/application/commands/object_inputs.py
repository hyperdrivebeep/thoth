"""Decision object RPC input models with unchanged validation and defaults."""

from __future__ import annotations

from pydantic import Field, JsonValue

from thoth.domain.base import DomainModel


class ProjectInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)


class ObjectListInput(ProjectInput):
    thread_id: str | None = Field(default=None, max_length=160)
    lifecycle: str | None = Field(default=None, max_length=40)
    active_work_mode: str | None = Field(default=None, max_length=60)
    profile_ref: str | None = Field(default=None, max_length=160)
    attention: str | None = Field(default=None, max_length=60)
    blocker: str | None = Field(default=None, max_length=160)


class ObjectReadInput(ProjectInput):
    object_id: str = Field(min_length=1, max_length=160)
    revision_digest: str | None = Field(default=None, min_length=64, max_length=64)


class CandidateListInput(ProjectInput):
    thread_id: str | None = Field(default=None, max_length=160)
    candidate_state: str | None = Field(default=None, max_length=40)
    trigger_type: str | None = Field(default=None, max_length=80)


class CandidateReadInput(ProjectInput):
    candidate_id: str = Field(min_length=1, max_length=160)


class ProfileListInput(ProjectInput):
    domain_hint: str | None = Field(default=None, max_length=160)
    enabled_only: bool = True


class ProfileReadInput(ProjectInput):
    profile_ref: str = Field(min_length=1, max_length=160)
    version: int | None = Field(default=None, ge=1)


class RelationListInput(ObjectReadInput):
    relation_type: str | None = Field(default=None, max_length=80)
    target_namespace: str | None = Field(default=None, max_length=80)
    authority_state: str | None = Field(default=None, max_length=40)


class AttentionListInput(ProjectInput):
    attention_type: str | None = Field(default=None, max_length=80)
    risk: str | None = Field(default=None, max_length=40)


class AuditReadInput(ObjectReadInput):
    pass


class MaterializeInput(ProjectInput):
    thread_id: str = Field(min_length=1, max_length=160)
    candidate_id: str | None = Field(default=None, max_length=160)
    purpose_statement: str = Field(min_length=1, max_length=2_000)
    problem_frame: str | None = Field(default=None, max_length=5_000)
    focus_refs: tuple[str, ...]
    trigger_evidence_refs: tuple[str, ...]
    profile_refs: tuple[str, ...] = ()
    expected_project_revision: int | None = Field(default=None, ge=0)
    actor_ref: str = Field(default="agent:object-materializer", max_length=160)


class RevisionBoundInput(ObjectReadInput):
    expected_revision_digest: str = Field(min_length=64, max_length=64)


class FrameReviseInput(RevisionBoundInput):
    frame_patch: dict[str, JsonValue]
    evidence_refs: tuple[str, ...]
    reason: str = Field(min_length=1, max_length=5_000)


class ProfileApplyInput(RevisionBoundInput):
    profile_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    reason: str = Field(min_length=1, max_length=5_000)


class FacetUpdateInput(RevisionBoundInput):
    add: tuple[str, ...] = ()
    remove: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...]
    reason: str = Field(min_length=1, max_length=5_000)


class RelationAddInput(RevisionBoundInput):
    relation_type: str = Field(min_length=1, max_length=80)
    target_ref: str = Field(min_length=1, max_length=260)
    semantic_role: str = Field(min_length=1, max_length=260)
    evidence_refs: tuple[str, ...]
    valid_time: str | None = Field(default=None, max_length=64)
    authority_state: str = Field(pattern=r"^(UNCLASSIFIED|INFORMAL|OFFICIAL|APPROVED)$")
    actor_ref: str = Field(default="agent:relation-proposer", max_length=160)


class RelationRemoveInput(RevisionBoundInput):
    relation_id: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=5_000)
    evidence_refs: tuple[str, ...]


class RevalidateInput(ObjectReadInput):
    trigger_reason: str = Field(min_length=1, max_length=2_000)


class WorkReplanInput(ObjectReadInput):
    trigger_refs: tuple[str, ...]
    budget_policy_ref: str | None = Field(default=None, max_length=160)


class SplitProposeInput(RevisionBoundInput):
    partitions: tuple[dict[str, JsonValue], ...]
    evidence_refs: tuple[str, ...]
    rationale: str = Field(min_length=1, max_length=5_000)


class MergeProposeInput(ProjectInput):
    object_ids: tuple[str, ...] = Field(min_length=2)
    field_mapping: dict[str, JsonValue]
    evidence_refs: tuple[str, ...]
    rationale: str = Field(min_length=1, max_length=5_000)
    expected_revision_digests: tuple[str, ...] = Field(min_length=2)


class AttentionAcknowledgeInput(RevisionBoundInput):
    attention_id: str = Field(min_length=1, max_length=160)
    actor_ref: str = Field(min_length=1, max_length=160)
    note: str | None = Field(default=None, max_length=2_000)


class FollowupCreateInput(RevisionBoundInput):
    purpose_statement: str = Field(min_length=1, max_length=2_000)
    trigger_refs: tuple[str, ...]
    inherit_scope: bool = True
