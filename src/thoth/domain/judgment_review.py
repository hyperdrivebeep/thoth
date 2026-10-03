"""A person's request to re-examine one hypothesis judgment, and how it was resolved.

A request never changes a relation by itself: the AI re-judges through the normal research loop
and the outcome (upheld, changed, on hold) is recorded as a separate resolution.
"""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.hypothesis_full import HypothesisRecord
from thoth.domain.ids import Identifier, ProjectId, Sha256

ReviewReason = Literal[
    "EVIDENCE_INTERPRETATION",
    "MISSING_KEY_EVIDENCE",
    "SOURCE_OR_TIME_INAPPROPRIATE",
    "WRONG_RELATION_TO_OTHER_HYPOTHESES",
    "INSUFFICIENT_EXPLANATION",
    "OTHER",
]
REVIEW_REASON_LABELS: dict[str, str] = {
    "EVIDENCE_INTERPRETATION": "근거 해석이 다름",
    "MISSING_KEY_EVIDENCE": "중요한 근거가 빠짐",
    "SOURCE_OR_TIME_INAPPROPRIATE": "출처·시점이 부적절함",
    "WRONG_RELATION_TO_OTHER_HYPOTHESES": "다른 가설과의 관계가 잘못됨",
    "INSUFFICIENT_EXPLANATION": "설명이 부족함",
    "OTHER": "기타",
}
ReviewStatus = Literal["OPEN", "REVIEWING", "RESOLVED", "CANCELLED"]
InstructionState = Literal["NOT_SENT", "ACCEPTED", "FAILED"]
ReviewOutcome = Literal["UPHELD", "CHANGED", "HOLD"]
OPEN_STATUSES: frozenset[str] = frozenset({"OPEN", "REVIEWING"})
JUDGMENT_REVIEW_NAMESPACE = "JUDGMENT_REVIEW"
JUDGMENT_REVIEW_RECORD_TYPE = "REQUEST"


class JudgmentReviewResolution(DomainModel):
    outcome: ReviewOutcome
    previous_relation: str
    resulting_relation: str
    rationale: str
    evidence_refs: tuple[str, ...] = ()
    result_revision_digest: Sha256 | None = None
    resolved_at: AwareDatetime
    schema_version: str = "1.0.0"


class JudgmentReviewRequest(DomainModel):
    request_id: str
    project_id: ProjectId
    thread_id: Identifier
    hypothesis_id: Identifier
    hypothesis_revision_digest: Sha256
    evidence_ref: str | None = None
    reason_codes: tuple[ReviewReason, ...] = Field(min_length=1, max_length=6)
    note: str = Field(default="", max_length=2_000)
    added_evidence_refs: tuple[str, ...] = Field(default=(), max_length=20)
    requested_by: str = Field(min_length=1, max_length=160)
    status: ReviewStatus = "OPEN"
    previous_relation: str
    instruction_state: InstructionState = "NOT_SENT"
    instruction_operation_id: str | None = None
    instruction_status: str | None = None
    instruction_failure: str | None = None
    attempts: int = Field(default=0, ge=0)
    created_at: AwareDatetime
    resolution_ref: str | None = None
    resolution: JudgmentReviewResolution | None = None
    schema_version: str = "1.0.0"

    @model_validator(mode="after")
    def consistent(self) -> JudgmentReviewRequest:
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("review reason codes must be distinct")
        if (self.status == "RESOLVED") != (self.resolution is not None):
            raise ValueError("a resolved review carries exactly one resolution")
        return self


def relation_text(record: HypothesisRecord | None, evidence_ref: str | None) -> str:
    """How the hypothesis relates to evidence, as recorded. Never a score."""

    if record is None:
        return "REMOVED"
    if evidence_ref is not None:
        if evidence_ref in record.counterevidence_refs:
            return "COUNTER"
        return "SUPPORT" if evidence_ref in record.evidence_refs else "UNASSESSED"
    return (
        f"SUPPORT:{len(record.evidence_refs)}|COUNTER:{len(record.counterevidence_refs)}"
        f"|APPRAISAL:{record.empirical_appraisal}"
    )


def outcome_for(
    *, previous: str, resulting: str, answer_status: str | None, in_result: bool
) -> tuple[ReviewOutcome, str]:
    """Outcome and a fixed-wording rationale. A held answer is HOLD whatever the relation."""

    if answer_status is not None and "HOLD" in answer_status.upper():
        return "HOLD", "재검토 결과 판단을 보류했습니다."
    if not in_result:
        return "CHANGED", "재검토 결과의 가설 목록에 이 판단이 더는 없습니다."
    if previous == resulting:
        return "UPHELD", "재검토 뒤에도 관계가 그대로입니다."
    return "CHANGED", "재검토 뒤 관계가 바뀌었습니다."
