from __future__ import annotations

from pydantic import AwareDatetime, Field, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.ids import ProjectId, Sha256


class ObjectProfileRecord(DomainModel):
    profile_ref: str
    version: int = Field(ge=1)
    name: str
    domain_hint: str
    namespace_owner: str
    required_fields: tuple[str, ...]
    conditional_fields: tuple[str, ...] = ()
    allowed_facets: tuple[str, ...]
    allowed_relation_types: tuple[str, ...]
    entry_policy: dict[str, str]
    exit_policy: dict[str, str]
    enabled: bool = True
    migration_state: str = "CURRENT"
    profile_digest: Sha256
    schema_version: str = "1.0.0"


class ObjectCandidateRecord(DomainModel):
    candidate_id: str
    project_id: ProjectId
    thread_id: str
    purpose_statement: str
    problem_frame: str
    focus_refs: tuple[str, ...]
    trigger_type: str
    trigger_evidence_refs: tuple[str, ...]
    profile_candidates: tuple[str, ...]
    profile_state: str
    duplicate_object_refs: tuple[str, ...]
    entry_validation: str
    permitted_transition: str
    candidate_state: str = "PROPOSED"
    candidate_digest: Sha256
    created_at: AwareDatetime
    schema_version: str = "1.0.0"


class DecisionObjectRecord(DomainModel):
    object_revision_id: str
    object_id: str
    project_id: ProjectId
    thread_id: str
    parent_object_id: str | None = None
    purpose_statement: str
    problem_frame: str
    focus_refs: tuple[str, ...]
    workstream_refs: tuple[str, ...] = ()
    requirement_refs: tuple[str, ...] = ()
    cutoff_ref: str | None = None
    security_scope: str = "PROJECT_POLICY"
    profile_refs: tuple[str, ...] = ()
    profile_state: str = "UNCLASSIFIED"
    facets: tuple[str, ...] = ()
    materialization_trigger: str
    trigger_evidence_refs: tuple[str, ...]
    applicability: str = "PROJECT_SCOPED"
    entry_criteria: dict[str, str]
    exit_criteria: dict[str, str]
    active_work_mode: str = "FRAMING"
    lifecycle: str = "ACTIVE"
    resolution: str = "OPEN"
    verification: str = "NOT_ASSESSED"
    disposition: str = "NONE"
    freshness: str = "CURRENT"
    attention: str = "NONE"
    blockers: tuple[str, ...] = ()
    relation_refs: tuple[str, ...] = ()
    actor_or_agent_ref: str
    protected_boundary_context: dict[str, str] = Field(default_factory=dict)
    revision_digest: Sha256
    supersedes_revision_digest: Sha256 | None = None
    receipt_ref: str | None = None
    created_at: AwareDatetime
    schema_version: str = "1.0.0"

    @model_validator(mode="after")
    def keep_status_axes_independent(self) -> DecisionObjectRecord:
        if self.lifecycle == "CLOSED" and self.resolution not in {
            "RESOLVED",
            "ABSTAINED",
            "DEFERRED",
        }:
            raise ValueError("closed object requires an explicit resolution state")
        if self.resolution == "RESOLVED" and self.verification == "VERIFIED":
            return self
        return self


class ObjectRelationRecord(DomainModel):
    relation_revision_id: str
    relation_id: str
    project_id: ProjectId
    source_object_id: str
    relation_type: str
    target_ref: str
    semantic_role: str
    evidence_refs: tuple[str, ...]
    actor_or_agent_ref: str
    valid_from: AwareDatetime
    valid_to: AwareDatetime | None = None
    authority_state: str
    active: bool = True
    revision_digest: Sha256
    supersedes_revision_digest: Sha256 | None = None
    created_at: AwareDatetime
    schema_version: str = "1.0.0"


class ObjectAttentionRecord(DomainModel):
    attention_revision_id: str
    attention_id: str
    project_id: ProjectId
    object_id: str
    attention_type: str
    risk: str
    reason: str
    condition_ref: str
    state: str = "OPEN"
    acknowledged_by: str | None = None
    acknowledgement_note: str | None = None
    revision_digest: Sha256
    supersedes_revision_digest: Sha256 | None = None
    created_at: AwareDatetime
    schema_version: str = "1.0.0"


class ObjectAuditRecord(DomainModel):
    audit_id: str
    project_id: ProjectId
    object_id: str
    event_type: str
    payload: dict[str, object]
    event_digest: Sha256
    created_at: AwareDatetime
    schema_version: str = "1.0.0"
