"""Check a trace origin against the stored trace, and what a research request does with it."""

from __future__ import annotations

from pydantic import ValidationError

from thoth.application.services.verification_trace import (
    VerificationTraceService,
    mark_stale,
)
from thoth.domain.research_execution import ResearchWork
from thoth.domain.trace_origin import (
    MAX_COMPARISON_REFS,
    MAX_ORIGIN_REFS,
    MAX_SIBLING_SPAN_REFS,
    MAX_SIBLING_VERDICTS,
    TraceOriginInput,
    TraceSiblingVerdict,
    TraceVerdictOrigin,
)
from thoth.domain.verification_trace import (
    CriterionRule,
    SubjectKind,
    TraceKind,
    TraceRelation,
    VerdictRevision,
    VerificationTraceRecord,
    subject_key,
)

# Told to the model with the row: the verdict is the rule's, not a question for the model.
ORIGIN_NOTE = (
    "This investigation started from one row of the project's verification trace. The verdict was "
    "computed by the trace rule from stored results; do not change, re-judge or soften it. Use the "
    "row's reasons, rule and source positions as the starting context for finding candidate causes "
    "and the tests that would tell them apart. sibling_verdicts, when present, are the same "
    "measure under the other conditions of the same requirement, computed by the same rule: "
    "compare what differs between them and this row."
)


class TraceOriginRefused(ValueError):
    """The origin does not match the stored trace; the message is the reason code."""


def build_trace_origin(
    service: VerificationTraceService, project_id: str, claimed: TraceOriginInput
) -> TraceVerdictOrigin:
    """The origin the server writes for a row the client named, or the reason it is refused."""
    if claimed.project_id != project_id:
        raise TraceOriginRefused("TRACE_ORIGIN_PROJECT_MISMATCH")
    digest, record = service.read(project_id)
    if record is None or digest is None:
        raise TraceOriginRefused("TRACE_ORIGIN_NOT_FOUND")
    kind = SubjectKind(claimed.subject_kind)
    shown = mark_stale(record.trace_set, record.pending_changes)
    history = record.history.get(subject_key(kind, claimed.subject_id), ())
    if (kind, claimed.subject_id) not in shown or not history:
        raise TraceOriginRefused("TRACE_ORIGIN_NOT_FOUND")
    if claimed.verdict_revision != history[-1]:
        # A revision that was once this row's verdict is "changed"; one that never was is unknown.
        raise TraceOriginRefused(
            "TRACE_ORIGIN_REVISION_CHANGED"
            if claimed.verdict_revision in history
            else "TRACE_ORIGIN_NOT_FOUND"
        )
    verdict = service.revision(project_id, history[-1])
    return _from_stored(
        project_id, record, verdict, _siblings(service, project_id, record, verdict)
    )


def _siblings(
    service: VerificationTraceService,
    project_id: str,
    record: VerificationTraceRecord,
    verdict: VerdictRevision,
) -> tuple[tuple[TraceSiblingVerdict, ...], tuple[str, ...]]:
    """The other conditions of this criterion's measure under its requirement, and where their
    results are written. Found from the trace's links and rules only: criteria that refine the same
    requirement, have a rule with the same measure and another condition. Those with a chosen result
    come first, then the trace's own order; at most MAX_SIBLING_VERDICTS."""
    if verdict.subject_kind is not SubjectKind.CRITERION:
        return (), ()
    trace_set = record.trace_set
    rules = {rule.criterion_id: rule for rule in trace_set.rules}
    own = rules.get(verdict.subject_id)
    if own is None:
        return (), ()
    parents = {
        link.to_id
        for link in trace_set.links
        if link.relation is TraceRelation.REFINES and link.from_id == verdict.subject_id
    }
    found: list[tuple[TraceSiblingVerdict, tuple[str, ...]]] = []
    for item in trace_set.items:
        if item.kind is not TraceKind.CRITERION or item.item_id == verdict.subject_id:
            continue
        rule = rules.get(item.item_id)
        shares_parent = any(
            link.relation is TraceRelation.REFINES
            and link.from_id == item.item_id
            and link.to_id in parents
            for link in trace_set.links
        )
        digests = record.history.get(subject_key(SubjectKind.CRITERION, item.item_id), ())
        if rule is None or not shares_parent or not digests:
            continue
        if rule.measure != own.measure or rule.condition == own.condition:
            continue
        found.append(_sibling(service, project_id, record, rule, digests[-1]))
    found.sort(key=lambda entry: entry[0].result_id is None)  # stable: the trace's order is kept
    chosen = found[:MAX_SIBLING_VERDICTS]
    refs = tuple(dict.fromkeys(ref for _, spans in chosen for ref in spans))[:MAX_COMPARISON_REFS]
    return tuple(entry[0] for entry in chosen), refs


def _sibling(
    service: VerificationTraceService,
    project_id: str,
    record: VerificationTraceRecord,
    rule: CriterionRule,
    digest: str,
) -> tuple[TraceSiblingVerdict, tuple[str, ...]]:
    verdict = service.revision(project_id, digest)
    chosen = (
        () if verdict.selection is None else tuple(c.result_id for c in verdict.selection.chosen)
    )
    result = next(
        (r for r in record.trace_set.results if chosen and r.result_id == chosen[-1]), None
    )
    summary = f"{rule.measure} {rule.comparator.value} {format(rule.threshold, 'f')} {rule.unit}"
    sibling = TraceSiblingVerdict(
        criterion_id=rule.criterion_id,
        condition=rule.condition[:500],
        state=verdict.state.value,
        rule_summary=summary[:500],
        result_id=None if result is None else result.result_id[:500],
        value=None if result is None or result.value is None else format(result.value, "f"),
        numerator=None if result is None else result.numerator,
        denominator=None if result is None else result.denominator,
        unit=None if result is None else result.unit[:100],
    )
    return sibling, tuple(verdict.basis.source_span_refs[:MAX_SIBLING_SPAN_REFS])


def _from_stored(
    project_id: str,
    record: VerificationTraceRecord,
    verdict: VerdictRevision,
    siblings: tuple[tuple[TraceSiblingVerdict, ...], tuple[str, ...]] = ((), ()),
) -> TraceVerdictOrigin:
    trace_set = record.trace_set
    title = next(
        (
            item.title
            for item in trace_set.items
            if item.item_id == verdict.subject_id
            and item.kind in (TraceKind.CRITERION, TraceKind.REQUIREMENT)
        ),
        "",
    )
    applied = verdict.conditions
    rule = next((r for r in trace_set.rules if r.rule_id == applied.rule_id), None)
    summary = None
    if rule is not None and applied.comparator is not None and applied.threshold is not None:
        summary = (
            f"{rule.measure} {applied.comparator.value} {applied.threshold} {applied.unit or ''}"
        )
    chosen = (
        () if verdict.selection is None else tuple(c.result_id for c in verdict.selection.chosen)
    )
    return TraceVerdictOrigin(
        project_id=project_id,
        trace_set_digest=trace_set.set_digest,
        subject_kind=verdict.subject_kind.value,
        subject_id=verdict.subject_id,
        subject_title=title[:500],
        verdict_revision=verdict.revision_digest,
        verdict_digest=verdict.verdict_digest,
        state=verdict.state.value,
        reason_codes=tuple(code[:300] for code in verdict.reasons.computed[:MAX_ORIGIN_REFS]),
        condition=applied.condition,
        rule_summary=None if summary is None else summary.strip(),
        chosen_result_ids=chosen[:MAX_ORIGIN_REFS],
        source_span_refs=tuple(verdict.basis.source_span_refs[:MAX_ORIGIN_REFS]),
        sibling_verdicts=siblings[0],
        comparison_span_refs=siblings[1],
    )


def origin_context(origin: TraceVerdictOrigin) -> dict[str, object]:
    """The origin as the model's context and the result carry it."""
    return {**origin.model_dump(mode="json"), "note": ORIGIN_NOTE}


def apply_trace_origin(
    work: ResearchWork, continuation: dict[str, object], project_id: str
) -> None:
    """Give the request's origin to the model's context and pin its source positions.

    Only an origin that has the server's own shape for this project is used; anything else is left
    out and the result says so, instead of passing client text to the model.
    """
    raw = continuation.get("origin")
    if raw is None:
        return
    try:
        origin = TraceVerdictOrigin.model_validate(raw)
    except ValidationError:
        work.context["origin_rejected"] = "TRACE_ORIGIN_INVALID"
        return
    if origin.project_id != project_id:
        work.context["origin_rejected"] = "TRACE_ORIGIN_PROJECT_MISMATCH"
        return
    work.context["origin"] = origin_context(origin)
    work.pinned_spans = tuple(
        dict.fromkeys((*origin.source_span_refs, *origin.comparison_span_refs))
    )
