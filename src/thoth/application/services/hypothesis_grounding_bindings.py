"""Bind final rows to their actual supplied evidence and the pending result's pinned sources."""

from thoth.application.services.hypothesis_grounding_hold import GroundingPublicationHold
from thoth.domain.hypothesis_v5 import ExpectedResultV5, StoredExpectedResultV5
from thoth.domain.prediction_grounding import GroundingSourceBinding, RelationAssessmentV1
from thoth.domain.research_execution import ResearchWork, research_work


def grounding_work() -> ResearchWork:
    work = research_work.get()
    if (
        work is None
        or work.hypothesis_grounding is None
        or "GENERATOR" not in work.grounding_stages
    ):
        raise GroundingPublicationHold("GROUNDING_GENERATING_REQUEST_REQUIRED")
    return work


def bind_grounded_row(
    owner_id: str,
    test_id: str,
    row: ExpectedResultV5,
    work: ResearchWork,
) -> StoredExpectedResultV5:
    accepted = work.hypothesis_grounding
    if accepted is None:
        raise GroundingPublicationHold("GROUNDING_ACCEPTED_PORTFOLIO_MISSING")
    cell = (owner_id, test_id, row.hypothesis_id)
    stage = accepted.row_stages.get(cell)
    capture = None if stage is None else work.grounding_stages.get(stage)
    if stage is None or capture is None:
        raise GroundingPublicationHold("GROUNDING_SOURCE_STAGE_MISSING")
    if capture.metadata.generating_request_ref != work.request_ref:
        raise GroundingPublicationHold("GROUNDING_STAGE_REQUEST_MISMATCH")
    originals = [
        original
        for owner in accepted.portfolio.hypotheses
        if owner.hypothesis_id == owner_id
        for test in owner.discriminating_tests
        if test.test_id == test_id
        for original in test.expected_by_hypothesis
        if original.hypothesis_id == row.hypothesis_id
    ]
    if len(originals) != 1 or originals[0] != row:
        raise GroundingPublicationHold("GROUNDING_TARGET_CHANGED")
    supplied = {span.span_id: span for span in capture.evidence}
    published = {span.span_id: span for span in work.evidence}
    if len(published) != len(work.evidence):
        raise GroundingPublicationHold("GROUNDING_MANIFEST_SPAN_AMBIGUOUS")
    bindings: list[GroundingSourceBinding] = []
    for identifier in row.basis:
        span, final = supplied.get(identifier), published.get(identifier)
        if (
            span is None
            or final is None
            or span.project_id != work.request_ref.project_id
            or final.project_id != span.project_id
            or (span.source_version_id, span.text_sha256)
            != (final.source_version_id, final.text_sha256)
        ):
            raise GroundingPublicationHold("GROUNDING_MANIFEST_SOURCE_MISMATCH")
        bindings.append(
            GroundingSourceBinding(
                span_id=span.span_id,
                source_version_id=span.source_version_id,
                text_sha256=span.text_sha256,
            )
        )
    return StoredExpectedResultV5.model_validate(
        {
            **row.model_dump(),
            "source_stage": stage,
            "source_bindings": bindings,
            "relation_assessment": RelationAssessmentV1(),
        }
    )


def require_grounding_publication(work: ResearchWork | None) -> None:
    """Run before the cycle UoW writes, and again before publishing its result manifest."""
    if work is None or work.hypothesis_grounding is None:
        return
    for owner in work.hypothesis_grounding.portfolio.hypotheses:
        for test in owner.discriminating_tests:
            for row in test.expected_by_hypothesis:
                bind_grounded_row(owner.hypothesis_id, test.test_id, row, work)
