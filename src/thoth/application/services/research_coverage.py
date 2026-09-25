"""Fail-closed coverage reduction over separate candidate and adjudicator runs."""

from thoth.application.services.research_conflicts import reduce_scoped_conflicts
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.evidence_gap import GapValidation
from thoth.domain.evidence_requirements import (
    CoverageAssessment,
    RequirementAssessment,
    RequirementSetRevision,
    ReviewAdjudication,
    ReviewProposal,
    SemanticReviewDecision,
    SemanticReviewRecord,
)
from thoth.domain.research_request import RevisionRef


def answer_assessment_state(coverage: CoverageAssessment) -> str:
    if coverage.schema_version == "2.0.0" and coverage.conflicts:
        return "PARTIAL_HOLD"
    critical = {
        "MANDATORY_RULE_COVERAGE_MISSING",
        "PROFILE_DECISION_REQUIRED",
        "REQUIREMENT_INTERPRETATION_INCOMPLETE",
        "REQUIRED_CONTEXT_OMITTED",
        "REQUIRED_SCOPE_UNEXAMINED",
    }
    if critical.intersection(coverage.reasons) or any(
        coverage.gates.get(target) == "HOLD" for target in ("answer", "time")
    ):
        return "PARTIAL_HOLD"
    return "ASSESSED_WITH_OPEN_CHECKS" if coverage.reasons else "ASSESSED_FOR_REQUEST"


def assess_coverage(
    requirements: RequirementSetRevision,
    requirements_ref: RevisionRef,
    candidate: ReviewProposal,
    adjudication: ReviewAdjudication,
    evidence: tuple[EvidenceSpan, ...],
    run: str,
    explicit_web: bool,
    *,
    gap_validations: tuple[GapValidation, ...] = (),
) -> tuple[CoverageAssessment, tuple[SemanticReviewRecord, ...]]:
    allowed = {s.span_id for s in evidence}
    proposals = {c.requirement_id: c for c in candidate.candidates}
    decisions = {c.requirement_id: c for c in adjudication.decisions}
    duplicate = len(proposals) != len(candidate.candidates) or len(decisions) != len(
        adjudication.decisions
    )
    assessments: list[RequirementAssessment] = []
    records: list[SemanticReviewRecord] = []
    gates: dict[str, str] = {}
    for req in requirements.requirements:
        unresolved_context = any(
            g.requirement_id == req.requirement_id and g.effective_disposition == "UNRESOLVED"
            for g in gap_validations
        )
        item, decision = proposals.get(req.requirement_id), decisions.get(req.requirement_id)
        resolution = "UNRESOLVED"
        valid = bool(
            item
            and decision
            and not duplicate
            and set(item.evidence_refs) <= allowed
            and set(item.applicability_basis) <= allowed
        )
        if item is not None and decision is not None:
            if valid and decision.verdict == "APPLIED":
                if (
                    req.kind == "RESEARCH_CHECK"
                    and item.applicability == "NOT_APPLICABLE_CANDIDATE"
                    and item.applicability_basis
                    and decision.applicability_confirmed
                ):
                    resolution = "NOT_APPLICABLE"
                elif (
                    item.applicability == "APPLICABLE"
                    and item.evidence_refs
                    and item.relation in {"SUPPORTS", "REFUTES", "QUALIFIES"}
                    and not item.missing_context
                    and item.conditions_checked
                    and item.time_checked
                    and (not req.counterevidence_required or item.counterevidence_checked)
                ):
                    resolution = "SATISFIED"
            if not valid:
                decision = SemanticReviewDecision(
                    requirement_id=req.requirement_id,
                    verdict="REJECTED",
                    explanation="Unknown references or duplicate review IDs",
                )
            if unresolved_context:
                resolution = "UNRESOLVED"
            records.append(
                SemanticReviewRecord(
                    request_ref=requirements.request_ref,
                    target_digest=requirements_ref.revision_digest,
                    candidate=item,
                    decision=decision,
                    structural_checks=(
                        "EXACT_REQUEST",
                        "KNOWN_EVIDENCE" if valid else "INVALID_REFERENCES",
                    ),
                    reviewer_run=run,
                    independence_group="SAME_PRODUCT_MODEL_SEPARATE_ROLE",
                    support_effect=resolution,
                )
            )
        assessments.append(
            RequirementAssessment(
                requirement_id=req.requirement_id,
                acquisition="FOUND"
                if item and item.evidence_refs
                else "NOT_FOUND_IN_EXAMINED_SCOPE",
                presence="PRESENT" if item and item.evidence_refs else "MISSING",
                applicability="UNKNOWN" if item is None else item.applicability,
                relation="INSUFFICIENT" if item is None else item.relation,
                validation="NOT_REVIEWED" if decision is None else decision.verdict,
                resolution=resolution,
                blocker=req.blocker,
            )
        )
        if resolution == "UNRESOLVED" and req.kind == "BOUND_OBLIGATION":
            gates[req.target] = "HOLD"
        else:
            gates.setdefault(req.target, "ASSESSED")
    covered = {r for req in requirements.requirements for r in req.rule_refs}
    rules_valid = bool(
        requirements.requirements
        and requirements.mandatory_rule_coverage
        and set(requirements.mandatory_rule_coverage) <= covered
    )
    reasons: list[str] = []
    if not rules_valid:
        reasons.append("MANDATORY_RULE_COVERAGE_MISSING")
    if requirements.unresolved_profile_choices:
        reasons.append("PROFILE_DECISION_REQUIRED")
        gates["profile_dependent_promotion"] = "HOLD"
    if not adjudication.decomposition_complete or adjudication.missing_requirements:
        reasons.append("REQUIREMENT_INTERPRETATION_INCOMPLETE")
    if any(a.resolution == "UNRESOLVED" for a in assessments):
        reasons.append("REQUIREMENT_GAPS")
    if any(
        g.requirement_id is None and g.effective_disposition == "UNRESOLVED"
        for g in gap_validations
    ):
        reasons.append("GAP_MAPPING_INCOMPLETE")
    if any(
        g.effective_disposition == "UNRESOLVED"
        and any(
            r.requirement_id == g.requirement_id and r.kind == "BOUND_OBLIGATION"
            for r in requirements.requirements
        )
        for g in gap_validations
    ):
        reasons.append("REQUIRED_CONTEXT_OMITTED")
    if candidate.missing_required_scope:
        reasons.append("REQUIRED_SCOPE_UNEXAMINED")
    conflicts = reduce_scoped_conflicts(
        requirements, requirements_ref, candidate, adjudication, allowed
    )
    for conflict in conflicts:
        for target in conflict.blocked_targets:
            gates[target] = "HOLD"
    if any(conflict.blocked_targets for conflict in conflicts):
        reasons.append("EVIDENCE_CONFLICT")
    elif conflicts:
        reasons.append("REVIEWED_CONFLICT_HISTORY")
    if explicit_web:
        reasons.append("EXPLICIT_PUBLIC_SEARCH")
    return CoverageAssessment(
        request_ref=requirements.request_ref,
        requirement_set_ref=requirements_ref,
        assessments=tuple(assessments),
        web_decision="REQUIRED"
        if any(reason != "REVIEWED_CONFLICT_HISTORY" for reason in reasons)
        else "SKIPPED_SUFFICIENT",
        reasons=tuple(reasons),
        gates=gates,
        unexamined_scope=candidate.unexamined_scope,
        conflicts=candidate.conflicts,
        scoped_conflicts=conflicts,
        gap_validations=gap_validations,
        allowed_next_steps=tuple(
            req.followup
            for req, a in zip(requirements.requirements, assessments, strict=True)
            if a.resolution == "UNRESOLVED"
        )
        + tuple(c.next_check for c in conflicts if c.blocked_targets),
    ), tuple(records)
