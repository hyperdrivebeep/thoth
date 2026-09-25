"""Public history v2 envelopes. Views never become a second research authority."""

from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.ids import Sha256
from thoth.domain.research_basis import BasisCurrentness
from thoth.domain.revision import SemanticDiffEntry


class HistoryScope(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)
    thread_id: str | None = None
    request_revision_digest: Sha256 | None = None
    entity_ref: str | None = None


class HistoryCoverage(DomainModel):
    visibility: Literal["AUTHORIZED_SUBSET"] = "AUTHORIZED_SUBSET"
    association: Literal["EXACT", "PARTIAL", "UNRESOLVED"] = "EXACT"
    scan: Literal["COMPLETE_PAGE", "CONTINUATION", "LIMITED"] = "COMPLETE_PAGE"
    reasons: tuple[str, ...] = ()


class HistoryRecordRef(DomainModel):
    owner_kind: Literal["SEMANTIC_REVISION", "FULL_MEMORY"]
    project_id: str
    immutable_id: str
    revision_digest: Sha256


class HistoryScopeLink(DomainModel):
    thread_id: str
    request_revision_digest: Sha256
    association: Literal["PRODUCED_IN", "USED_BY", "RELATED_OBJECT"]


class HistoryCapability(DomainModel):
    preview_supported: bool = False
    restore: Literal["RESTORE_SUPPORTED", "READ_ONLY", "UNSUPPORTED"] = "READ_ONLY"
    apply_ready: bool = False
    reason_codes: tuple[str, ...] = ()


class HistoryItem(DomainModel):
    item_id: str
    kind: Literal["REQUEST", "RESULT", "REVISION", "RESTORE", "MEMORY"]
    occurred_at: AwareDatetime
    record_ref: HistoryRecordRef
    operation_id: str | None = None
    origin_request_revision_digest: Sha256 | None = None
    scope_links: tuple[HistoryScopeLink, ...] = ()
    association: Literal["PRODUCED_IN", "USED_BY", "RELATED_OBJECT", "UNATTRIBUTED"]
    head_membership: Literal["CURRENT", "ANCESTOR", "BRANCH", "UNKNOWN"]
    title: str
    availability: Literal["AVAILABLE", "PARTIAL", "UNAVAILABLE"] = "AVAILABLE"
    entity_type: str | None = None
    entity_id: str | None = None
    schema_family: str | None = None
    completion: Literal["CHECKPOINT", "TERMINAL"] | None = None
    phase: str | None = None
    currentness: BasisCurrentness
    capability: HistoryCapability = Field(default_factory=HistoryCapability)


class HistoryTimelineInput(DomainModel):
    project_id: str
    scope: HistoryScope
    kinds: tuple[Literal["REQUEST", "RESULT", "REVISION", "RESTORE", "MEMORY"], ...] = ()
    cursor: str | None = None
    limit: int = Field(default=25, ge=1, le=50)
    contract_version: Literal[2] = 2


class HistoryItemInput(DomainModel):
    project_id: str
    scope: HistoryScope
    record_ref: HistoryRecordRef
    contract_version: Literal[2] = 2


class HistoryPage(DomainModel):
    contract_version: Literal[2] = 2
    items: tuple[HistoryItem, ...]
    coverage: HistoryCoverage
    next_cursor: str | None = None
    capability: HistoryCapability = Field(default_factory=HistoryCapability)
    actor_scope_digest: Sha256


class HistoryDetail(DomainModel):
    contract_version: Literal[2] = 2
    item: HistoryItem
    content: dict[str, object] | None
    coverage: HistoryCoverage
    current_head_digest: Sha256 | None = None


class HistoryDiff(DomainModel):
    contract_version: Literal[2] = 2
    from_revision_digest: Sha256
    to_revision_digest: Sha256
    diff: tuple[SemanticDiffEntry, ...]
    summary_groups: tuple["SemanticChangeGroup", ...]
    coverage: HistoryCoverage


class HistoricalResultInput(DomainModel):
    project_id: str
    thread_id: str
    request_revision_digest: Sha256
    result_revision_digest: Sha256 | None = None
    include_selected_evidence: bool = False
    selected_evidence_offset: int = Field(default=0, ge=0)
    selected_evidence_limit: int = Field(default=50, ge=1, le=100)
    contract_version: Literal[2] = 2

    @model_validator(mode="after")
    def selected_evidence_requires_pinned_result(self) -> "HistoricalResultInput":
        if self.include_selected_evidence and self.result_revision_digest is None:
            raise ValueError("SELECTED_EVIDENCE_RESULT_DIGEST_REQUIRED")
        return self


class SelectedEvidenceItem(DomainModel):
    span_id: str
    availability: Literal["AVAILABLE", "UNAVAILABLE", "BASIS_MISMATCH", "UNKNOWN_BASIS"]
    reason_codes: tuple[str, ...] = ()
    span: EvidenceSpan | None = None

    @model_validator(mode="after")
    def unavailable_has_no_source_bytes(self) -> "SelectedEvidenceItem":
        if (self.availability == "AVAILABLE") != (self.span is not None):
            raise ValueError("SELECTED_EVIDENCE_AVAILABILITY_MISMATCH")
        if self.span is not None and self.span.span_id != self.span_id:
            raise ValueError("SELECTED_EVIDENCE_ID_MISMATCH")
        return self


class SelectedEvidencePage(DomainModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    result_revision_digest: Sha256
    actor_scope_digest: Sha256
    selected_count: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    next_offset: int | None = None
    items: tuple[SelectedEvidenceItem, ...]


class HistoricalResultView(DomainModel):
    contract_version: Literal[2] = 2
    project_id: str
    thread_id: str
    request_revision_digest: Sha256
    operation_id: str
    result_revision_digest: Sha256 | None = None
    request: dict[str, object]
    manifest: dict[str, object] | None = None
    result: dict[str, object] | None = None
    error: dict[str, object] | None = None
    operation_state: str | None = None
    execution_observation: Literal["UNKNOWN"] = "UNKNOWN"
    availability: Literal["AVAILABLE", "PARTIAL", "UNAVAILABLE"]
    basis_currentness: BasisCurrentness
    coverage: HistoryCoverage
    selected_evidence: SelectedEvidencePage | None = None


class SemanticChangeGroup(DomainModel):
    kind: Literal["CONTENT", "EVIDENCE", "CONDITION", "STATUS", "IMPACT", "OTHER"]
    trace_paths: tuple[str, ...]
    changes: tuple[SemanticDiffEntry, ...]
    summary: str


class HistoryDiffInput(DomainModel):
    project_id: str
    from_revision_digest: Sha256
    to_revision_digest: Sha256
    contract_version: Literal[2] = 2
