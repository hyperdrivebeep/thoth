"""Freeze scopes, check completed reviewer claims and retain their original stage provenance."""

from datetime import datetime

from thoth.application.services.request_records import RequestRecords
from thoth.application.services.research_absence_checks import (
    assess_absence_claims,
    has_checkable_absence_claims,
    make_absence_scope,
)
from thoth.application.services.research_absence_usage import admit_absence_catalog
from thoth.application.services.research_findings import adopt_confirmed_findings
from thoth.application.services.research_retrieval_policy import retrieval_policy
from thoth.application.services.research_stages import read_stage
from thoth.domain.absence_claim import (
    AbsenceAssessments,
    AbsenceClaimAssessment,
    AbsenceRun,
    ReviewProposalE2,
)
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import ModelRole
from thoth.domain.evidence import EvidenceSpan, connected_retrieval_spans
from thoth.domain.model import ContextPack
from thoth.domain.project import Project
from thoth.domain.research_execution import ResearchWork
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.source_version_catalog import SourceVersionCatalogPort


def initialize_absence(
    work: ResearchWork, project: Project, source: tuple[EvidenceSpan, ...], now: datetime
) -> None:
    if work.absence is not None:
        return
    source = connected_retrieval_spans(source)
    work.absence = AbsenceRun(
        make_absence_scope(
            work.request_ref,
            "ALLOWED_EXTRACTED_AT_REQUEST",
            source,
            captured_at=now,
            phase="ANALYSIS_START_BEFORE_MEMORY_EXPANSION",
            policy_ref=project.policy_binding_ref,
            cutoff_at=project.cutoff_at,
            policy_digest=domain_digest(
                "ABSENCE_RETRIEVAL_POLICY", "1.0.0", canonical_payload(retrieval_policy())
            ),
            admitted_refs=frozenset(),
            reasons=("ABSENCE_SCOPE_NOT_CHECKED_YET",),
        ),
        source,
    )


def capture_absence_input(
    work: ResearchWork | None, role: ModelRole, context: ContextPack, now: datetime
) -> None:
    if work is not None and work.absence is not None and role == ModelRole.SEMANTIC_REVIEWER:
        work.absence.actual_spans = context.evidence
        work.absence.actual_captured_at = now


def assess_review_absence(
    work: ResearchWork,
    proposal: ReviewProposalE2,
    records: RequestRecords,
    current: tuple[EvidenceSpan, ...],
    artifacts: ArtifactLedgerPort,
) -> None:
    state = work.absence
    if state is None:
        return
    work.boundary.check()
    stage_ref = work.record_refs[-1]
    stage = read_stage(records, stage_ref)
    if stage.request_ref != work.request_ref or stage.role != "SEMANTIC_REVIEWER":
        raise ValueError("ABSENCE_REVIEW_STAGE_MISMATCH")
    if tuple(s.span_id for s in state.actual_spans) != stage.evidence_refs:
        raise ValueError("ABSENCE_ACTUAL_INPUT_MISMATCH")
    allowed = state.allowed_scope
    current = connected_retrieval_spans(current)
    checkable = has_checkable_absence_claims(proposal)
    b_admitted, b_reasons = (
        admit_absence_catalog(
            state,
            state.allowed_spans,
            artifacts.read_evidence,
            artifacts if isinstance(artifacts, SourceVersionCatalogPort) else None,
        )
        if checkable
        else (frozenset[str](), ("ABSENCE_NO_CHECKABLE_CLAIMS",))
    )
    a = make_absence_scope(
        work.request_ref,
        "ACTUAL_ROLE_INPUT",
        state.actual_spans,
        captured_at=state.actual_captured_at or records.clock.now(),
        phase="REUSED_STAGE_INPUT" if stage.reused_from else "BEFORE_REVIEW_DISPATCH",
        policy_ref=allowed.policy_ref,
        cutoff_at=allowed.cutoff_at,
        policy_digest=allowed.retrieval_policy_digest,
        current=current,
        stage_ref=stage_ref,
        reasons=(() if checkable else ("ABSENCE_NO_CHECKABLE_CLAIMS",))
        + (() if state.actual_captured_at else ("ABSENCE_ACTUAL_INPUT_UNAVAILABLE",)),
        admitted_refs=None if checkable else frozenset(),
    )
    b = make_absence_scope(
        work.request_ref,
        "ALLOWED_EXTRACTED_AT_REQUEST",
        state.allowed_spans,
        captured_at=allowed.captured_at,
        phase=allowed.capture_phase,
        policy_ref=allowed.policy_ref,
        cutoff_at=allowed.cutoff_at,
        policy_digest=allowed.retrieval_policy_digest,
        current=current,
        admitted_refs=b_admitted,
        reasons=b_reasons,
    )
    checked = set((*a.checked_refs, *b.checked_refs))
    for span in (*state.actual_spans, *state.allowed_spans):
        if span.span_id in checked:
            prior = work.consumed_sources.get(span.span_id)
            if prior is not None and prior != span:
                raise ValueError("ABSENCE_CONSUMED_SOURCE_CHANGED")
            state.checked_spans[span.span_id] = span
            work.consumed_sources.setdefault(span.span_id, span)
    result = assess_absence_claims(
        work.request_ref, proposal, stage_ref, a, state.actual_spans, b, state.allowed_spans
    )
    for ref in dict.fromkeys(
        r
        for item in result.items
        for outcome in (item.a_result, item.b_result)
        for r in outcome.witness_refs
    ):
        if artifacts.read_evidence(ref) != state.checked_spans[ref]:
            raise ValueError("ABSENCE_WITNESS_SOURCE_CHANGED")
    work.boundary.check()
    raw = work.result_only.get("absence_assessments")
    if raw is not None:
        prior_result = AbsenceAssessments.model_validate(raw)
        scopes = {s.scope_digest: s for s in (*prior_result.scope_sets, *result.scope_sets)}
        items = (*prior_result.items, *result.items)
        result = AbsenceAssessments(
            request_ref=work.request_ref,
            active_stage_ref=stage_ref,
            scope_sets=tuple(scopes.values()),
            items=items,
            not_checked_count=sum(
                i.a_result.outcome == "NOT_CHECKED" or i.b_result.outcome == "NOT_CHECKED"
                for i in items
            ),
            reason_codes=tuple(sorted(set((*prior_result.reason_codes, *result.reason_codes)))),
        )
    work.result_only["absence_assessments"] = result.model_dump(mode="json")


def active_absence_items(work: ResearchWork) -> tuple[AbsenceClaimAssessment, ...]:
    raw = work.result_only.get("absence_assessments")
    if raw is None:
        return ()
    result = AbsenceAssessments.model_validate(raw)
    return tuple(i for i in result.items if i.origin_stage_ref == result.active_stage_ref)


def blocked_absence_findings(work: ResearchWork) -> frozenset[int]:
    return frozenset(
        i.claim.finding_index
        for i in active_absence_items(work)
        if i.warning_code is not None
        and i.claim.origin == "CONFIRMED_FINDING"
        and i.claim.finding_index is not None
    )


def adopt_absence_checked_findings(
    proposal: ReviewProposalE2, work: ResearchWork, reviewer_input: tuple[EvidenceSpan, ...]
) -> None:
    contradicted = {i.claim.statement for i in active_absence_items(work) if i.warning_code}
    blocked = blocked_absence_findings(work) | frozenset(
        index
        for index, finding in enumerate(proposal.confirmed_findings)
        if finding.statement in contradicted
    )
    findings, dropped = adopt_confirmed_findings(
        proposal.confirmed_findings,
        {s.span_id for s in reviewer_input},
        blocked_indexes=blocked,
    )
    work.context.update(confirmed_findings=findings, confirmed_findings_dropped=dropped)


def has_absence_hold(work: ResearchWork) -> bool:
    return any(i.warning_code is not None for i in active_absence_items(work))


def attach_absence_result(result: dict[str, object], work: ResearchWork) -> dict[str, object]:
    raw = work.result_only.get("absence_assessments")
    if raw is None:
        return result
    updated = {**result, "absence_assessments": raw}
    warnings = tuple(sorted({i.warning_code for i in active_absence_items(work) if i.warning_code}))
    if warnings:
        updated.update(answer_status="PARTIAL_HOLD", absence_warning_codes=list(warnings))
    return updated
