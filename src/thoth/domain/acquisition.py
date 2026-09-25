from __future__ import annotations

from pydantic import AwareDatetime, Field, JsonValue

from thoth.domain.base import DomainModel
from thoth.domain.connectors import ConnectorReceipt, ConnectorRunRecord
from thoth.domain.control_record import ControlRecord
from thoth.domain.evidence_basis import EvidenceCommitBasis
from thoth.domain.evidence_graph import (
    EvidenceAuditRecord,
    EvidenceLinkRecord,
    EvidenceSourceRecord,
    ObservationRecord,
)
from thoth.domain.governance import SourceBinding
from thoth.domain.ids import ProjectId, Sha256, ThreadId
from thoth.domain.ingestion import IngestionResult
from thoth.domain.project import Project


class AcquisitionCommit(DomainModel):
    ingestion: IngestionResult
    source_binding: SourceBinding
    evidence_source: EvidenceSourceRecord
    evidence_audit: EvidenceAuditRecord
    project_after: Project
    expected_project_revision: int = Field(ge=0)
    connector_run: ConnectorRunRecord
    connector_receipt: ConnectorReceipt
    run_record: ControlRecord
    receipt_record: ControlRecord
    transformation_records: tuple[ControlRecord, ...] = ()


class SearchIntent(DomainModel):
    search_intent_id: str = Field(min_length=1, max_length=160)
    project_id: ProjectId
    thread_id: ThreadId
    investigation_id: str = Field(min_length=1, max_length=160)
    evidence_group: str = Field(min_length=1, max_length=160)
    query_families: tuple[str, ...] = Field(min_length=1)
    connector_id: str = Field(min_length=1, max_length=160)
    selector: dict[str, JsonValue]
    max_waves: int = Field(ge=1, le=8)
    max_results: int = Field(ge=1, le=50)
    stop_conditions: tuple[str, ...] = Field(min_length=1)
    policy_id: str = Field(min_length=1, max_length=160)
    policy_revision: int = Field(ge=1)
    policy_digest: Sha256
    state: str = Field(default="READY", pattern=r"^(READY|EXECUTING|COMPLETED|HELD)$")
    intent_digest: Sha256
    created_at: AwareDatetime


class AcquisitionLead(DomainModel):
    lead_id: str = Field(min_length=1, max_length=160)
    project_id: ProjectId
    investigation_id: str = Field(min_length=1, max_length=160)
    search_intent_id: str = Field(min_length=1, max_length=160)
    source_id: str = Field(min_length=1, max_length=160)
    span_id: str = Field(min_length=1, max_length=160)
    evidence_group: str = Field(min_length=1, max_length=160)
    state: str = Field(pattern=r"^(OPEN|CLAIM_CANDIDATE|REJECTED|EXHAUSTED)$")
    information_value: str = Field(min_length=1, max_length=80)
    authority_basis: str = Field(min_length=1, max_length=500)
    lead_digest: Sha256
    created_at: AwareDatetime


class EvidenceAtomicCommit(DomainModel):
    basis: EvidenceCommitBasis
    observation: ObservationRecord
    lead: AcquisitionLead | None = None
    claim_candidate: EvidenceLinkRecord
    audit_records: tuple[EvidenceAuditRecord, ...] = Field(min_length=1)
