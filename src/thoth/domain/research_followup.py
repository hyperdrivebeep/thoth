"""Read-only user follow-up projections over stored research records."""

from typing import Literal

from pydantic import Field, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.ids import Sha256
from thoth.domain.research_basis import BasisCurrentness
from thoth.domain.research_reference import RevisionRef
from thoth.domain.revision import SemanticDiffEntry


class NextUserAction(DomainModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    action_type: Literal[
        "NONE",
        "REVIEW_GAPS",
        "REVIEW_CURRENTNESS",
        "OPEN_RESULT_DETAIL",
        "START_FOLLOWUP",
        "UNSUPPORTED",
    ]
    label: str
    reason_codes: tuple[str, ...] = ()
    target: dict[str, object] | None = None
    requires_permission: bool = False
    basis: dict[str, object] = Field(default_factory=dict)


class CoverageMatrixRow(DomainModel):
    requirement_id: str
    target: str
    question: str
    status: Literal["SATISFIED", "UNRESOLVED", "NOT_APPLICABLE", "NOT_ASSESSED"]
    applicability: str
    relation: str
    validation: str
    blocker: str
    review_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()


class CoverageMatrixSummary(DomainModel):
    satisfied: int = 0
    unresolved: int = 0
    not_applicable: int = 0
    not_assessed: int = 0
    hold_targets: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()


class CoverageMatrix(DomainModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    request_revision_digest: Sha256
    requirement_set_revision_digest: Sha256 | None = None
    coverage_revision_digest: Sha256 | None = None
    rows: tuple[CoverageMatrixRow, ...] = ()
    summary: CoverageMatrixSummary = Field(default_factory=CoverageMatrixSummary)
    availability: Literal["AVAILABLE", "PARTIAL", "UNAVAILABLE"] = "AVAILABLE"
    reason_codes: tuple[str, ...] = ()


class UserProgressSummary(DomainModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    request_revision_digest: Sha256
    result_revision_digest: Sha256 | None = None
    state: Literal[
        "PENDING_OR_NOT_PRODUCED",
        "IN_PROGRESS",
        "COMPLETE",
        "NEEDS_REVIEW",
        "HOLD",
        "FAILED",
        "CANCELLED",
        "UNKNOWN",
    ]
    currentness: BasisCurrentness
    progress_items: tuple[str, ...] = ()
    recorded_checks: tuple[str, ...] = ()
    remaining_gaps: tuple[str, ...] = ()
    unknowns: tuple[str, ...] = ()
    next_user_action: NextUserAction


class ResultIdentity(DomainModel):
    request_revision_digest: Sha256
    result_revision_digest: Sha256


class DecisionDeltaInput(DomainModel):
    project_id: str
    thread_id: str
    before: ResultIdentity
    after: ResultIdentity
    contract_version: Literal[2] = 2

    @model_validator(mode="after")
    def require_distinct_results(self) -> "DecisionDeltaInput":
        if self.before == self.after:
            raise ValueError("RESULT_COMPARE_REQUIRES_TWO_IDENTITIES")
        return self


class DecisionDeltaGroup(DomainModel):
    kind: Literal["CONTENT", "EVIDENCE", "CONDITION", "STATUS", "ACTION", "OTHER"]
    trace_paths: tuple[str, ...]
    changes: tuple[SemanticDiffEntry, ...]


class DecisionDelta(DomainModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    contract_version: Literal[2] = 2
    project_id: str
    thread_id: str
    before: ResultIdentity
    after: ResultIdentity
    state: Literal["CHANGED", "NO_CHANGE", "PARTIAL", "UNAVAILABLE"]
    groups: tuple[DecisionDeltaGroup, ...] = ()
    reason_state: Literal["RECORDED", "UNKNOWN_REASON"]
    reason_codes: tuple[str, ...] = ()
    reason_refs: tuple[RevisionRef, ...] = ()
    basis_currentness: dict[str, BasisCurrentness]


class ProjectReviewListInput(DomainModel):
    project_id: str
    cursor: str | None = None
    limit: int = Field(default=25, ge=1, le=50)
    contract_version: Literal[2] = 2


class ProjectReviewItem(DomainModel):
    item_id: str
    project_id: str
    thread_id: str
    request_revision_digest: Sha256
    result_revision_digest: Sha256 | None = None
    title: str
    priority: Literal["HIGH", "MEDIUM", "LOW"]
    reason_codes: tuple[str, ...]
    currentness: BasisCurrentness
    next_user_action: NextUserAction


class ProjectReviewList(DomainModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    contract_version: Literal[2] = 2
    project_id: str
    items: tuple[ProjectReviewItem, ...]
    next_cursor: str | None = None
    coverage: Literal["COMPLETE_PAGE", "CONTINUATION", "LIMITED"]
    unread_supported: Literal[False] = False
    assignment_supported: Literal[False] = False
