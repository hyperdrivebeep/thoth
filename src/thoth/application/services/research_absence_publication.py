"""Bind literal-check records to completed proposals and the existing result owner."""

from thoth.application.services.request_records import RequestRecords
from thoth.application.services.research_absence_checks import claim_origin
from thoth.application.services.research_stages import read_stage
from thoth.domain.absence_claim import AbsenceAssessments, AbsenceSourceRef, ReviewProposalE2
from thoth.domain.research_execution import ResearchWork


def validate_absence_publication(
    result: dict[str, object], work: ResearchWork, records: RequestRecords
) -> None:
    raw = result.get("absence_assessments")
    if raw is None:
        return
    observed = AbsenceAssessments.model_validate(raw)
    if observed.request_ref != work.request_ref or work.absence is None:
        raise ValueError("ABSENCE_PUBLICATION_REQUEST_MISMATCH")
    original_members = work.absence.allowed_scope.members
    for scope in observed.scope_sets:
        if scope.kind == "ALLOWED_EXTRACTED_AT_REQUEST" and scope.members != original_members:
            raise ValueError("ABSENCE_ALLOWED_SNAPSHOT_CHANGED")
        members = {r.span_id: r for r in scope.members}
        for ref in scope.checked_refs:
            span = work.absence.checked_spans.get(ref)
            if (
                span is None
                or AbsenceSourceRef.from_span(span) != members[ref]
                or work.consumed_sources.get(ref) != span
            ):
                raise ValueError("ABSENCE_CHECKED_SOURCE_UNBOUND")
    stages = {i.origin_stage_ref.revision_digest: i.origin_stage_ref for i in observed.items}
    stages[observed.active_stage_ref.revision_digest] = observed.active_stage_ref
    for ref in stages.values():
        stage = read_stage(records, ref)
        if (
            stage.request_ref != work.request_ref
            or stage.output_codec != "ReviewProposalE2"
            or stage.role != "SEMANTIC_REVIEWER"
        ):
            raise ValueError("ABSENCE_PUBLICATION_STAGE_MISMATCH")
        candidate = ReviewProposalE2.model_validate(stage.output)
        by_id = {
            f"{ref.revision_id}:{index}": claim
            for index, claim in enumerate(candidate.absence_claims)
        }
        scope = next((s for s in observed.scope_sets if s.stage_ref == ref), None)
        if scope is None or tuple(r.span_id for r in scope.members) != stage.evidence_refs:
            raise ValueError("ABSENCE_PUBLICATION_INPUT_MISMATCH")
        for item in (i for i in observed.items if i.origin_stage_ref == ref):
            if by_id.get(item.claim_id) != item.claim or claim_origin(candidate, item.claim) != (
                item.origin_pointer,
                item.origin_bound,
            ):
                raise ValueError("ABSENCE_PUBLICATION_ORIGIN_MISMATCH")


def summarize_absence_assessments(raw: object) -> dict[str, object]:
    result = AbsenceAssessments.model_validate(raw)
    items = tuple(i for i in result.items if i.origin_stage_ref == result.active_stage_ref)
    return {
        "schema_version": result.schema_version,
        "checker_version": result.checker_version,
        "prose_check_coverage": result.prose_check_coverage,
        "items": [
            {
                "target": i.claim.target,
                "statement": i.claim.statement,
                "target_language_basis": i.claim.target_language_basis,
                "a_outcome": i.a_result.outcome,
                "b_outcome": i.b_result.outcome,
                "effective": i.effective,
                "warning_code": i.warning_code,
                "injection_state": i.injection_state,
            }
            for i in items
        ],
        "not_checked_count": sum(
            i.a_result.outcome == "NOT_CHECKED" or i.b_result.outcome == "NOT_CHECKED"
            for i in items
        ),
    }
