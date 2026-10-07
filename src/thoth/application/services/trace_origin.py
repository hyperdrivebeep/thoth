"""Check a trace origin against the stored trace, and what a research request does with it."""

from __future__ import annotations

from pydantic import ValidationError

from thoth.application.services.verification_trace import (
    VerificationTraceService,
    mark_stale,
)
from thoth.domain.research_execution import ResearchWork
from thoth.domain.trace_origin import MAX_ORIGIN_REFS, TraceOriginInput, TraceVerdictOrigin
from thoth.domain.verification_trace import (
    SubjectKind,
    TraceKind,
    VerdictRevision,
    VerificationTraceRecord,
    subject_key,
)

# Told to the model with the row: the verdict is the rule's, not a question for the model.
ORIGIN_NOTE = (
    "This investigation started from one row of the project's verification trace. The verdict was "
    "computed by the trace rule from stored results; do not change, re-judge or soften it. Use the "
    "row's reasons, rule and source positions as the starting context for finding candidate causes "
    "and the tests that would tell them apart."
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
    return _from_stored(project_id, record, service.revision(project_id, history[-1]))


def _from_stored(
    project_id: str, record: VerificationTraceRecord, verdict: VerdictRevision
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
    work.pinned_spans = origin.source_span_refs
