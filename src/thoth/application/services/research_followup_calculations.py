"""Pure coverage, progress and next-action projections over already-authorized records."""

from collections.abc import Iterable, Mapping
from typing import Literal

from thoth.domain.evidence_requirements import (
    CoverageAssessment,
    EvidenceRequirement,
    RequirementAssessment,
    RequirementSetRevision,
    SemanticReviewRecord,
)
from thoth.domain.research_basis import BasisCurrentness
from thoth.domain.research_followup import (
    CoverageMatrix,
    CoverageMatrixRow,
    CoverageMatrixSummary,
    NextUserAction,
)
from thoth.domain.research_reference import RevisionRef
from thoth.domain.research_request import (
    CurrentResultManifest,
    CurrentResultManifestV21,
    ResearchAttempt,
)

# Actual callers are the original read facade and the pure delta calculation.
__all__ = [
    "ResultManifest",
    "_basis_currentness",
    "_coverage_matrix",
    "_next_action",
    "_progress_items",
    "_progress_state",
    "_recorded_checks",
    "_remaining_gaps",
    "_unknowns",
]


ResultManifest = CurrentResultManifest | CurrentResultManifestV21


ProgressState = Literal[
    "PENDING_OR_NOT_PRODUCED",
    "IN_PROGRESS",
    "COMPLETE",
    "NEEDS_REVIEW",
    "HOLD",
    "FAILED",
    "CANCELLED",
    "UNKNOWN",
]


def _coverage_matrix(
    request_ref: RevisionRef,
    records: Mapping[str, tuple[RevisionRef, object]],
) -> CoverageMatrix:
    requirement_entry = records.get("RequirementSetRevision")
    coverage_entry = records.get("CoverageAssessment")
    requirement_set = requirement_entry[1] if requirement_entry is not None else None
    coverage = coverage_entry[1] if coverage_entry is not None else None
    if not isinstance(requirement_set, RequirementSetRevision):
        return CoverageMatrix(
            request_revision_digest=request_ref.revision_digest,
            coverage_revision_digest=None
            if coverage_entry is None
            else coverage_entry[0].revision_digest,
            availability="PARTIAL" if coverage_entry is not None else "UNAVAILABLE",
            reason_codes=("REQUIREMENT_SET_UNAVAILABLE",),
        )
    if requirement_entry is None:
        raise ValueError("REQUIREMENT_ENTRY_MISSING")
    requirement_ref = requirement_entry[0]
    if not isinstance(coverage, CoverageAssessment):
        rows = tuple(
            CoverageMatrixRow(
                requirement_id=req.requirement_id,
                target=req.target,
                question=req.question,
                status="NOT_ASSESSED",
                applicability=req.applicability,
                relation="NOT_ASSESSED",
                validation="NOT_ASSESSED",
                blocker=req.blocker,
                reason_codes=("COVERAGE_UNAVAILABLE",),
            )
            for req in requirement_set.requirements
        )
        return CoverageMatrix(
            request_revision_digest=request_ref.revision_digest,
            requirement_set_revision_digest=requirement_ref.revision_digest,
            rows=rows,
            summary=_matrix_summary(rows, {}, ("COVERAGE_UNAVAILABLE",)),
            availability="PARTIAL",
            reason_codes=("COVERAGE_UNAVAILABLE",),
        )
    if coverage_entry is None:
        raise ValueError("COVERAGE_ENTRY_MISSING")
    reviews = _semantic_reviews(records)
    assessments = {item.requirement_id: item for item in coverage.assessments}
    rows = tuple(
        _matrix_row(req, assessments.get(req.requirement_id), reviews)
        for req in requirement_set.requirements
    )
    reason_codes = tuple(dict.fromkeys((*coverage.reasons, *coverage.allowed_next_steps)))
    return CoverageMatrix(
        request_revision_digest=request_ref.revision_digest,
        requirement_set_revision_digest=requirement_ref.revision_digest,
        coverage_revision_digest=coverage_entry[0].revision_digest,
        rows=rows,
        summary=_matrix_summary(rows, coverage.gates, reason_codes),
        availability="AVAILABLE",
        reason_codes=reason_codes,
    )


def _matrix_row(
    requirement: EvidenceRequirement,
    assessment: RequirementAssessment | None,
    reviews: Mapping[str, SemanticReviewRecord],
) -> CoverageMatrixRow:
    if assessment is None:
        return CoverageMatrixRow(
            requirement_id=requirement.requirement_id,
            target=requirement.target,
            question=requirement.question,
            status="NOT_ASSESSED",
            applicability=requirement.applicability,
            relation="NOT_ASSESSED",
            validation="NOT_ASSESSED",
            blocker=requirement.blocker,
            reason_codes=("ASSESSMENT_UNAVAILABLE",),
        )
    review_refs = assessment.review_refs
    evidence_refs: list[str] = []
    for ref in review_refs:
        review = reviews.get(ref)
        if review is not None:
            evidence_refs.extend(review.candidate.evidence_refs)
    return CoverageMatrixRow(
        requirement_id=requirement.requirement_id,
        target=requirement.target,
        question=requirement.question,
        status=assessment.resolution,
        applicability=assessment.applicability,
        relation=assessment.relation,
        validation=assessment.validation,
        blocker=assessment.blocker,
        review_refs=review_refs,
        evidence_refs=tuple(dict.fromkeys(evidence_refs)),
        reason_codes=()
        if assessment.resolution == "SATISFIED"
        else tuple(dict.fromkeys((assessment.blocker, assessment.validation))),
    )


def _semantic_reviews(
    records: Mapping[str, tuple[RevisionRef, object]],
) -> dict[str, SemanticReviewRecord]:
    reviews: dict[str, SemanticReviewRecord] = {}
    for _kind, (_ref, record) in records.items():
        if isinstance(record, SemanticReviewRecord):
            reviews[record.candidate.requirement_id] = record
    return reviews


def _matrix_summary(
    rows: Iterable[CoverageMatrixRow],
    gates: Mapping[str, str],
    reason_codes: tuple[str, ...],
) -> CoverageMatrixSummary:
    materialized = tuple(rows)
    return CoverageMatrixSummary(
        satisfied=sum(1 for row in materialized if row.status == "SATISFIED"),
        unresolved=sum(1 for row in materialized if row.status == "UNRESOLVED"),
        not_applicable=sum(1 for row in materialized if row.status == "NOT_APPLICABLE"),
        not_assessed=sum(1 for row in materialized if row.status == "NOT_ASSESSED"),
        hold_targets=tuple(target for target, state in gates.items() if state == "HOLD"),
        reason_codes=reason_codes,
    )


def _next_action(
    *,
    request_ref: RevisionRef,
    result_ref: RevisionRef | None,
    manifest: ResultManifest | None,
    currentness: BasisCurrentness,
    coverage: CoverageMatrix,
    operation_state: str,
) -> NextUserAction:
    basis: dict[str, object] = {
        "request_revision_digest": request_ref.revision_digest,
        "result_revision_digest": None if result_ref is None else result_ref.revision_digest,
    }
    if operation_state in {"FAILED", "CANCELLED"}:
        return NextUserAction(
            action_type="OPEN_RESULT_DETAIL",
            label="Review execution outcome",
            reason_codes=(operation_state,),
            target=basis,
            basis=basis,
        )
    if manifest is None:
        return NextUserAction(
            action_type="OPEN_RESULT_DETAIL",
            label="Wait for or inspect the current research attempt",
            reason_codes=("RESULT_NOT_PRODUCED",),
            target=basis,
            basis=basis,
        )
    if currentness.state != "CURRENT":
        return NextUserAction(
            action_type="REVIEW_CURRENTNESS",
            label="Review result currentness before using this answer",
            reason_codes=currentness.reasons,
            target=basis,
            basis=basis,
        )
    if (
        coverage.summary.unresolved
        or coverage.summary.not_assessed
        or coverage.summary.hold_targets
    ):
        return NextUserAction(
            action_type="REVIEW_GAPS",
            label="Review unresolved evidence gaps",
            reason_codes=tuple(
                dict.fromkeys(
                    (
                        *coverage.summary.reason_codes,
                        *coverage.summary.hold_targets,
                    )
                )
            ),
            target=basis,
            basis=basis,
        )
    return NextUserAction(action_type="NONE", label="No recorded review action", basis=basis)


def _progress_state(
    *,
    manifest: ResultManifest | None,
    attempt: ResearchAttempt | None,
    operation_state: str,
    currentness_state: str,
    coverage: CoverageMatrix,
) -> ProgressState:
    if operation_state == "FAILED":
        return "FAILED"
    if operation_state == "CANCELLED":
        return "CANCELLED"
    if manifest is None:
        if attempt is not None and operation_state == "RUNNING":
            return "IN_PROGRESS"
        return "PENDING_OR_NOT_PRODUCED"
    if manifest.phase == "HOLD" or coverage.summary.hold_targets:
        return "HOLD"
    if (
        currentness_state != "CURRENT"
        or coverage.summary.unresolved
        or coverage.summary.not_assessed
    ):
        return "NEEDS_REVIEW"
    if operation_state == "SUCCEEDED" and manifest.completion == "TERMINAL":
        return "COMPLETE"
    return "UNKNOWN"


def _progress_items(
    attempt: ResearchAttempt | None, manifest: ResultManifest | None
) -> tuple[str, ...]:
    items: list[str] = []
    if attempt is not None:
        items.extend(ref.entity_id for ref in attempt.completed_stage_refs)
    if manifest is not None:
        items.append(f"result:{manifest.completion.lower()}")
    return tuple(dict.fromkeys(items))


def _recorded_checks(
    records: Mapping[str, tuple[RevisionRef, object]], coverage: CoverageMatrix
) -> tuple[str, ...]:
    checks: list[str] = []
    if "RequirementSetRevision" in records:
        checks.append("requirements_recorded")
    if "CoverageAssessment" in records:
        checks.append("coverage_assessed")
    checks.extend(f"requirement:{row.requirement_id}:{row.status}" for row in coverage.rows)
    return tuple(checks)


def _remaining_gaps(
    manifest: ResultManifest | None,
    records: Mapping[str, tuple[RevisionRef, object]],
    coverage: CoverageMatrix,
) -> tuple[str, ...]:
    gaps: list[str] = []
    if manifest is not None:
        gaps.extend(manifest.gaps)
        gaps.extend(manifest.next_steps)
    coverage_entry = records.get("CoverageAssessment")
    if coverage_entry is not None and isinstance(coverage_entry[1], CoverageAssessment):
        assessment = coverage_entry[1]
        gaps.extend(assessment.reasons)
        gaps.extend(assessment.allowed_next_steps)
    gaps.extend(
        row.blocker for row in coverage.rows if row.status in {"UNRESOLVED", "NOT_ASSESSED"}
    )
    return tuple(item for item in dict.fromkeys(gaps) if item)


def _unknowns(
    manifest: ResultManifest | None,
    records: Mapping[str, tuple[RevisionRef, object]],
    coverage: CoverageMatrix,
) -> tuple[str, ...]:
    unknowns: list[str] = []
    if manifest is None:
        unknowns.append("RESULT_NOT_PRODUCED")
    elif not isinstance(manifest, CurrentResultManifestV21):
        unknowns.append("LEGACY_BASIS_UNKNOWN")
    if "RequirementSetRevision" not in records:
        unknowns.append("REQUIREMENT_SET_UNAVAILABLE")
    if "CoverageAssessment" not in records:
        unknowns.append("COVERAGE_UNAVAILABLE")
    if coverage.summary.not_assessed:
        unknowns.append("REQUIREMENT_NOT_ASSESSED")
    return tuple(dict.fromkeys(unknowns))


def _basis_currentness(value: Mapping[str, object]) -> BasisCurrentness:
    return BasisCurrentness.model_validate(value)
