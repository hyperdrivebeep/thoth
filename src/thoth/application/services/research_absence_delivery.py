"""Offer outside witnesses once to the existing adjudicator without dropping reviewer input."""

from collections.abc import Callable
from typing import cast

from thoth.application.services.research_absence_runtime import active_absence_items
from thoth.application.services.research_retrieval_policy import retrieval_policy
from thoth.application.services.research_role_context import bundle_view
from thoth.domain.absence_claim import ABSENCE_RESULT_CONTRACT, AbsenceAssessments
from thoth.domain.canonical import canonical_payload
from thoth.domain.enums import ModelRole
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.evidence_bundle import ContextAssembly
from thoth.domain.model import ContextPack
from thoth.domain.research_execution import ResearchWork

type Assemble = Callable[
    [ResearchWork, tuple[str, ...], tuple[EvidenceSpan, ...], tuple[EvidenceSpan, ...]],
    ContextAssembly,
]


def _mark_pending(work: ResearchWork, state: str, reason: str | None = None) -> None:
    result = AbsenceAssessments.model_validate(work.result_only["absence_assessments"])
    items = tuple(
        item.model_copy(update={"injection_state": state, "injection_reason": reason})
        if item.origin_stage_ref == result.active_stage_ref and item.injection_state == "PENDING"
        else item
        for item in result.items
    )
    updated = AbsenceAssessments.model_validate({**result.model_dump(), "items": items})
    work.result_only["absence_assessments"] = updated.model_dump(mode="json")


def prepare_absence_witness(
    context: ContextPack,
    work: ResearchWork,
    assemble: Assemble,
    source_packet: Callable[[tuple[EvidenceSpan, ...]], dict[str, object]],
) -> ContextPack:
    state = work.absence
    pending = tuple(i for i in active_absence_items(work) if i.injection_state == "PENDING")
    if state is None or not pending:
        return context
    if state.witness_attempted:
        _mark_pending(work, "ALREADY_ATTEMPTED", "ABSENCE_WITNESS_ALREADY_ATTEMPTED")
        return context
    state.witness_attempted = True
    work.boundary.check()
    present = {s.span_id: s for s in context.evidence}
    wanted = tuple(
        dict.fromkeys(ref for i in pending for ref in i.b_result.witness_refs if ref not in present)
    )
    pool = {**state.checked_spans, **present}
    if not set(wanted) <= pool.keys():
        _mark_pending(work, "NOT_DELIVERED", "ABSENCE_WITNESS_SOURCE_UNAVAILABLE")
        return context
    previous_selection = work.retrieval_selection
    assembly = assemble(work, (*present, *wanted), tuple(pool.values()), tuple(pool.values()))
    admitted = {s.span_id: s for s in assembly.evidence}
    if not set((*present, *wanted)) <= admitted.keys() or any(
        admitted.get(k) != s for k, s in present.items()
    ):
        work.retrieval_selection = previous_selection
        _mark_pending(work, "NOT_DELIVERED", "ABSENCE_WITNESS_POLICY_LIMIT")
        return context
    if assembly.wave.missing_structure_refs:
        work.retrieval_selection = previous_selection
        _mark_pending(work, "NOT_DELIVERED", "ABSENCE_WITNESS_STRUCTURE_UNAVAILABLE")
        return context
    evidence = (*context.evidence, *(s for s in assembly.evidence if s.span_id not in present))
    work.context["context_bundle"] = bundle_view(assembly)
    work.context["source_packet"] = source_packet(evidence)
    retrieval = cast(dict[str, object], work.context.get("retrieval", {}))
    work.context["retrieval"] = {**retrieval, "selected_count": len(evidence)}
    return context.model_copy(
        update={
            "evidence": evidence,
            "research_context": {
                **context.research_context,
                "context_bundle": work.context["context_bundle"],
                "source_packet": work.context["source_packet"],
            },
        }
    )


def complete_absence_witness(work: ResearchWork, context: ContextPack) -> None:
    pending = tuple(i for i in active_absence_items(work) if i.injection_state == "PENDING")
    if not pending:
        return
    refs = {s.span_id for s in context.evidence}
    if all(set(i.b_result.witness_refs) <= refs for i in pending):
        _mark_pending(work, "DELIVERED")
    else:
        _mark_pending(work, "NOT_DELIVERED", "ABSENCE_WITNESS_INPUT_MISMATCH")


def with_absence_checks(context: ContextPack, role: ModelRole, work: ResearchWork) -> ContextPack:
    if role != ModelRole.REVIEW_ADJUDICATOR:
        return context
    raw = work.result_only.get("absence_assessments")
    if raw is None:
        return context
    result = AbsenceAssessments.model_validate(raw)
    items = active_absence_items(work)
    refs = {s.span_id for s in context.evidence}
    view: dict[str, object] = {
        "schema_version": "1.0.0",
        "contract": ABSENCE_RESULT_CONTRACT,
        "coverage": result.prose_check_coverage,
        "claims": [
            {
                "candidate_claim_index": index,
                "a": item.a_result.outcome,
                "b": item.b_result.outcome,
                "warning": item.warning_code,
                "input_witness_refs": list(
                    dict.fromkeys(
                        r
                        for r in (*item.a_result.witness_refs, *item.b_result.witness_refs)
                        if r in refs
                    )
                ),
                "reasons": list(
                    dict.fromkeys(r for r in (item.a_result.reason, item.b_result.reason) if r)
                ),
            }
            for index, item in enumerate(items[:12])
        ],
        "not_checked_count": sum(
            i.a_result.outcome == "NOT_CHECKED" or i.b_result.outcome == "NOT_CHECKED"
            for i in items
        ),
        "omitted_claim_count": max(0, len(items) - 12),
    }
    numeric = context.research_context.get("checked_observations")
    used = sum(len(s.exact_text) for s in context.evidence) + (
        len(canonical_payload(cast(dict[str, object], numeric)).decode())
        if numeric is not None
        else 0
    )
    if used + len(canonical_payload(view).decode()) > retrieval_policy().character_budget:
        work.result_only["absence_assessments"] = result.model_copy(
            update={
                "reason_codes": tuple(sorted({*result.reason_codes, "ABSENCE_VIEW_POLICY_LIMIT"}))
            }
        ).model_dump(mode="json")
        return context
    return context.model_copy(
        update={"research_context": {**context.research_context, "absence_checks": view}}
    )
