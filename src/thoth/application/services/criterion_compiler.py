from __future__ import annotations

from thoth.domain.criterion import (
    CriterionCandidate,
    CriterionCompilationResult,
    CriterionCompilationSignals,
    CriterionDraft,
)
from thoth.domain.enums import (
    AuthorityState,
    CriterionCompilationStatus,
    CriterionOrigin,
    CutoffState,
)
from thoth.domain.evidence import EvidenceSpan

_REQUIRED_FIELDS = (
    "name",
    "measured_construct",
    "computation",
    "acceptance_rule",
    "context",
)


def compile_criterion(
    draft: CriterionDraft,
    *,
    evidence: tuple[EvidenceSpan, ...],
    signals: CriterionCompilationSignals,
) -> CriterionCompilationResult:
    evidence_by_id = {
        span.span_id: span for span in evidence if span.project_id == draft.project_id
    }
    referenced = tuple(
        dict.fromkeys(reference for refs in draft.field_evidence.values() for reference in refs)
    )
    invalid_refs = tuple(
        sorted(reference for reference in referenced if reference not in evidence_by_id)
    )
    missing = set(draft.missing_fields)
    for field in _REQUIRED_FIELDS:
        if not draft.field_evidence.get(field):
            missing.add(field)
    if draft.computation is None:
        missing.add("computation")
    if draft.acceptance_rule is None:
        missing.add("acceptance_rule")
    if not draft.context:
        missing.add("context")
    missing_fields = tuple(sorted(missing))

    if invalid_refs:
        return CriterionCompilationResult(
            status=CriterionCompilationStatus.REJECTED,
            invalid_evidence_refs=invalid_refs,
            missing_fields=missing_fields,
        )

    referenced_spans = tuple(evidence_by_id[reference] for reference in referenced)
    official_sources = bool(referenced_spans) and all(
        span.authority_state in {AuthorityState.OFFICIAL, AuthorityState.APPROVED}
        and span.cutoff_state == CutoffState.ELIGIBLE
        for span in referenced_spans
    )
    if draft.origin == CriterionOrigin.REFERENCE_PROPOSAL:
        candidate = CriterionCandidate(
            criterion_id=draft.criterion_id,
            project_id=draft.project_id,
            name=draft.name,
            measured_construct=draft.measured_construct,
            computation=draft.computation,
            context=draft.context,
            acceptance_rule=draft.acceptance_rule,
            evidence_refs=referenced,
            authority_state=(
                AuthorityState.INFORMAL if referenced_spans else AuthorityState.UNCLASSIFIED
            ),
            reference_candidate=True,
            evaluator_input_allowed=False,
        )
        return CriterionCompilationResult(
            status=CriterionCompilationStatus.REFERENCE_ONLY,
            candidate=candidate,
            missing_fields=missing_fields,
            expert_questions=(
                "Should this reference proposal be adopted through the project's "
                "formal change process?",
            ),
        )

    authority = AuthorityState.OFFICIAL if official_sources else AuthorityState.UNCLASSIFIED
    evaluator_ready = (
        not missing_fields
        and official_sources
        and signals.source_scope_confirmed
        and signals.expert_semantics_confirmed
        and signals.computation_verified
    )
    candidate = CriterionCandidate(
        criterion_id=draft.criterion_id,
        project_id=draft.project_id,
        name=draft.name,
        measured_construct=draft.measured_construct,
        computation=draft.computation,
        context=draft.context,
        acceptance_rule=draft.acceptance_rule,
        evidence_refs=referenced,
        authority_state=authority,
        reference_candidate=False,
        evaluator_input_allowed=evaluator_ready,
    )
    questions: list[str] = []
    if not signals.source_scope_confirmed:
        questions.append(
            "Do the cited spans belong to the controlling plan and applicable phase?"
        )
    if not signals.expert_semantics_confirmed:
        questions.append(
            "Does the extracted construct match the domain meaning of the official criterion?"
        )
    if not signals.computation_verified:
        questions.append(
            "Has the formula, unit, denominator and aggregation been independently verified?"
        )
    if missing_fields:
        questions.append("Which controlling document defines the missing criterion fields?")
    return CriterionCompilationResult(
        status=(
            CriterionCompilationStatus.EVALUATOR_READY
            if evaluator_ready
            else CriterionCompilationStatus.EXPERT_REVIEW_REQUIRED
        ),
        candidate=candidate,
        missing_fields=missing_fields,
        expert_questions=tuple(questions),
    )
