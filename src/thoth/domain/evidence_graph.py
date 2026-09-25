from __future__ import annotations

from pydantic import AwareDatetime, Field, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.enums import AuthorityState, CutoffState, SupportState, VerificationState
from thoth.domain.ids import ProjectId, Sha256


class EvidenceSourceRecord(DomainModel):
    source_id: str
    project_id: ProjectId
    artifact_id: str
    connector_ref: str
    uri: str
    artifact_type: str
    version: str | None = None
    sha256: Sha256
    authority_status: AuthorityState
    security_class: str
    official_copy: bool = False
    rights: str = "UNKNOWN"
    retention: str = "PROJECT_DEFAULT"
    valid_time: AwareDatetime | None = None
    snapshot_time: AwareDatetime | None = None
    cutoff_eligibility: CutoffState
    lineage_root_id: str
    parent_source_ids: tuple[str, ...] = ()
    supersedes_source_id: str | None = None
    source_digest: Sha256
    created_at: AwareDatetime
    schema_version: str = "1.0.0"


class ObservationRecord(DomainModel):
    observation_id: str
    project_id: ProjectId
    span_ids: tuple[str, ...]
    observed_statement: str
    observed_time: AwareDatetime | None = None
    valid_time: AwareDatetime | None = None
    observer_group: str
    independence_basis: str
    provenance_class: str
    contamination_note: str | None = None
    supersedes_observation_id: str | None = None
    observation_digest: Sha256
    created_at: AwareDatetime
    schema_version: str = "1.0.0"

    @model_validator(mode="after")
    def source_bound(self) -> ObservationRecord:
        if not self.span_ids:
            raise ValueError("observation requires source spans")
        return self


class EvidenceLinkRecord(DomainModel):
    evidence_id: str
    project_id: ProjectId
    thread_id: str | None = None
    target_type: str
    target_id: str
    relation: str
    source_ids: tuple[str, ...]
    span_ids: tuple[str, ...]
    observation_ids: tuple[str, ...] = ()
    conditions: dict[str, str] = Field(default_factory=dict)
    applicability: str = "WITHIN_STATED_CONDITIONS"
    independence_group: str
    support_status: SupportState = SupportState.SUPPORTED_CANDIDATE
    authority_status: AuthorityState = AuthorityState.UNCLASSIFIED
    verification_status: VerificationState = VerificationState.NOT_CHECKED
    cutoff_eligibility: CutoffState = CutoffState.UNKNOWN_TIME
    content_trust: str = "UNTRUSTED_EVIDENCE"
    conflict_ids: tuple[str, ...] = ()
    supersedes_evidence_id: str | None = None
    revision: int = Field(default=0, ge=0)
    evidence_digest: Sha256
    created_at: AwareDatetime
    schema_version: str = "1.0.0"

    @model_validator(mode="after")
    def enforce_relation_and_sources(self) -> EvidenceLinkRecord:
        if self.relation not in {
            "SUPPORTS",
            "CONTRADICTS",
            "QUALIFIES",
            "DEFINES",
            "MEASURES",
            "DERIVED_FROM",
        }:
            raise ValueError("unsupported evidence relation")
        if not self.source_ids or not self.span_ids:
            raise ValueError("evidence link requires source and span IDs")
        return self


class EvidenceConflictRecord(DomainModel):
    conflict_id: str
    project_id: ProjectId
    evidence_ids: tuple[str, ...]
    field: str
    status: str = "OPEN"
    reason: str
    resolution_evidence_ids: tuple[str, ...] = ()
    conflict_digest: Sha256
    created_at: AwareDatetime
    updated_at: AwareDatetime
    schema_version: str = "1.0.0"


class EvidenceAuditRecord(DomainModel):
    audit_id: str
    project_id: ProjectId
    evidence_ref: str
    event_type: str
    payload: dict[str, object]
    event_digest: Sha256
    created_at: AwareDatetime
    schema_version: str = "1.0.0"


class SpanCorrectionRecord(DomainModel):
    correction_id: str
    project_id: ProjectId
    span_id: str
    corrected_text: str
    reason: str
    correction_digest: Sha256
    created_at: AwareDatetime
    schema_version: str = "1.0.0"
