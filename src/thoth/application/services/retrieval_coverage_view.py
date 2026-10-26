"""One pure coverage builder for early analysis and direct cycle-model callers."""

from thoth.domain.enums import ModelRole
from thoth.domain.model import ContextPack
from thoth.domain.research_execution import ResearchWork
from thoth.domain.retrieval_coverage import RetrievalCoverage


def with_retrieval_coverage(
    context: ContextPack, role: ModelRole, work: ResearchWork
) -> ContextPack:
    selection = work.retrieval_selection
    if selection is None:
        return context  # No connected-source selection, e.g. memory query expansion.
    if context.project_id != work.request_ref.project_id:
        raise ValueError("RETRIEVAL_COVERAGE_PROJECT_MISMATCH")
    supplied = tuple(s.span_id for s in context.evidence)
    eligible = {s.span_id: s for s in selection.eligible}
    actual = set(supplied)
    matching = len(actual) == len(supplied) and all(
        eligible.get(s.span_id) == s for s in context.evidence
    )
    ranking = role == ModelRole.EVIDENCE_RERANKER
    anchors = selection.anchor_refs
    limitations = (*selection.limitations, *(("INPUT_SCOPE_DIFFERS",) if not matching else ()))
    coverage = RetrievalCoverage(
        request_ref=work.request_ref,
        source_basis_digest=selection.source_basis_digest,
        policy_digest=selection.policy_digest,
        role=role.value,
        phase="CANDIDATE_PRESENTATION" if ranking else "ASSEMBLED_INPUT",
        mode=selection.mode,
        catalog_count=len(selection.catalog),
        eligible_count=len(eligible),
        candidate_count=len(selection.candidate_refs),
        input_span_count=len(supplied),
        input_span_refs=supplied,
        ranked_anchor_count=None if ranking or anchors is None else len(anchors),
        selected_count=None if ranking else len(supplied),
        context_added_count=None if ranking or anchors is None else len(actual - set(anchors)),
        unselected_candidate_refs=tuple(s for s in selection.candidate_refs if s not in actual),
        omitted_anchor_refs=selection.omitted_anchor_refs,
        limitations=limitations,
        all_eligible_in_candidates=set(selection.candidate_refs) == set(eligible),
        coverage_state="UNKNOWN"
        if not matching
        else "COMPLETE_ELIGIBLE"
        if actual == set(eligible)
        else "PARTIAL",
    )
    return context.model_copy(
        update={
            "research_context": {
                **context.research_context,
                "retrieval_coverage": coverage.model_dump(mode="json"),
                "context_projection_version": "2.0.0",
            }
        }
    )
