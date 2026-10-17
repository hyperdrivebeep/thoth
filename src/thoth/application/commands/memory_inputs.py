"""Input contracts for memory_full; handlers and state transitions remain in their owner."""

from __future__ import annotations

from pydantic import AwareDatetime, Field

from thoth.domain.base import DomainModel


class ProjectInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)


class MemoryListInput(ProjectInput):
    recall_class: str | None = Field(default=None, max_length=80)
    payload_mode: str | None = Field(default=None, max_length=80)
    pipeline_status: str | None = Field(default=None, max_length=80)
    lifecycle_status: str | None = Field(default=None, max_length=80)
    recall_eligibility: str | None = Field(default=None, max_length=80)


class MemoryReadInput(ProjectInput):
    memory_entry_id: str = Field(min_length=1, max_length=160)
    revision_digest: str | None = Field(default=None, min_length=64, max_length=64)


class CandidateListInput(ProjectInput):
    producer_role: str | None = Field(default=None, max_length=80)
    classification_status: str | None = Field(default=None, max_length=80)
    risk_tag: str | None = Field(default=None, max_length=80)


class CandidateReadInput(ProjectInput):
    candidate_id: str = Field(min_length=1, max_length=160)


class ContextReadInput(ProjectInput):
    context_pack_id: str | None = Field(default=None, max_length=160)
    character_budget: int = Field(default=20_000, ge=0, le=2_000_000)


class PolicyListInput(ProjectInput):
    recall_class: str | None = Field(default=None, max_length=80)
    payload_mode: str | None = Field(default=None, max_length=80)
    enabled_only: bool = True


class PolicyReadInput(ProjectInput):
    profile_ref: str = Field(min_length=1, max_length=160)
    version: int | None = Field(default=None, ge=1)


class GateReadInput(ProjectInput):
    candidate_id: str | None = Field(default=None, max_length=160)
    memory_entry_id: str | None = Field(default=None, max_length=160)
    transition_receipt_ref: str | None = Field(default=None, max_length=160)


class ConflictListInput(ProjectInput):
    recall_class: str | None = Field(default=None, max_length=80)
    lifecycle_status: str | None = Field(default=None, max_length=80)


class RecallAuditInput(ProjectInput):
    context_pack_id: str | None = Field(default=None, max_length=160)
    thread_id: str | None = Field(default=None, max_length=160)
    time_range: dict[str, str] | None = None


class CandidateCreateInput(ProjectInput):
    producer_role: str = Field(min_length=1, max_length=80)
    payload_mode: str = Field(pattern=r"^(DOMAIN_REFERENCE|MEMORY_ASSERTION)$")
    recall_class: str = Field(min_length=1, max_length=80)
    domain_revision_ref: str | None = Field(default=None, max_length=160)
    assertion_candidate: str | None = Field(default=None, max_length=20_000)
    parent_refs: tuple[str, ...] = ()
    scope: dict[str, str]
    evidence_refs: tuple[str, ...]
    risk_tags: tuple[str, ...] = ()


class CandidateClassifyInput(CandidateReadInput):
    classifier_profile_ref: str = Field(min_length=1, max_length=160)
    expected_candidate_revision: int = Field(ge=1)


class ValidateInput(CandidateReadInput):
    transition_intent: str = Field(min_length=1, max_length=80)
    policy_version: str = Field(min_length=1, max_length=160)


class ChangeSetProposeInput(CandidateReadInput):
    transition_intent: str = Field(min_length=1, max_length=80)
    validated_gate_digest: str = Field(min_length=64, max_length=64)
    expected_memory_head_digest: str | None = Field(default=None, min_length=64, max_length=64)


class RevalidateInput(MemoryReadInput):
    trigger_refs: tuple[str, ...]


class RetireProposeInput(MemoryReadInput):
    reason: str = Field(min_length=1, max_length=2_000)
    evidence_refs: tuple[str, ...]
    expected_revision_digest: str = Field(min_length=64, max_length=64)


class ContextBuildInput(ProjectInput):
    thread_id: str = Field(min_length=1, max_length=160)
    query: str = Field(min_length=1, max_length=10_000)
    target_use: str = Field(min_length=1, max_length=80)
    scope: dict[str, str]
    token_budget: int = Field(ge=0, le=2_000_000)
    selection_policy_version: str = Field(min_length=1, max_length=160)
    cutoff: AwareDatetime | None = None


class ProjectionRebuildInput(ProjectInput):
    projection_types: tuple[str, ...]
    from_checkpoint: str | None = Field(default=None, max_length=160)
    model_or_index_version: str | None = Field(default=None, max_length=160)


class RetentionEvaluateInput(ProjectInput):
    scope: dict[str, str] | None = None
    policy_version: str = Field(min_length=1, max_length=160)
    evaluation_time: AwareDatetime
