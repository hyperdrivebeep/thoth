from __future__ import annotations

from datetime import datetime

from thoth.domain.base import DomainModel
from thoth.domain.criterion import CriterionCandidate
from thoth.domain.enums import (
    AuthorityState,
    CutoffState,
    DimensionStatus,
    SufficiencyStatus,
    SupportState,
    VerificationState,
)
from thoth.domain.evidence import (
    EvidenceSpan,
    InformationSufficiencyAssessment,
    SufficiencyDimension,
)


class SufficiencySignals(DomainModel):
    comparison_context_confirmed: bool | None = None
    counterevidence_checked: bool = False
    instrumentation_verified: bool | None = None
    expert_semantics_confirmed: bool = False


def assess_information_sufficiency(
    *,
    assessment_id: str,
    assessment_revision_id: str,
    project_id: str,
    target_object_id: str,
    cutoff_at: datetime,
    decision_question: str,
    criteria: tuple[CriterionCandidate, ...],
    evidence: tuple[EvidenceSpan, ...],
    signals: SufficiencySignals,
    policy_version: str,
    input_head_set_digest: str,
) -> InformationSufficiencyAssessment:
    scoped_evidence = tuple(
        span
        for span in evidence
        if span.project_id == project_id and span.cutoff_state == CutoffState.ELIGIBLE
    )
    admissible_criteria = tuple(
        criterion
        for criterion in criteria
        if criterion.project_id == project_id
        and criterion.authority_state in {AuthorityState.OFFICIAL, AuthorityState.APPROVED}
        and not criterion.reference_candidate
        and criterion.evaluator_input_allowed
    )
    complete_criteria = tuple(
        criterion
        for criterion in admissible_criteria
        if criterion.computation is not None and criterion.acceptance_rule is not None
    )
    supported = tuple(
        span
        for span in scoped_evidence
        if span.support_state == SupportState.SUPPORTED
        and span.verification_state
        in {VerificationState.PROVENANCE_VALID, VerificationState.DETERMINISTICALLY_VERIFIED}
    )

    scope = SufficiencyDimension(
        status=DimensionStatus.PASS if decision_question.strip() else DimensionStatus.FAIL,
        reason="decision scope is explicit"
        if decision_question.strip()
        else "decision question is empty",
    )
    criterion_authority = SufficiencyDimension(
        status=(
            DimensionStatus.PASS
            if complete_criteria
            else DimensionStatus.PARTIAL
            if criteria
            else DimensionStatus.FAIL
        ),
        evidence_refs=tuple(
            reference for criterion in complete_criteria for reference in criterion.evidence_refs
        ),
        reason=(
            "official evaluator-ready criterion exists"
            if complete_criteria
            else "criteria exist but official evaluator inputs are incomplete"
            if criteria
            else "no criterion candidate exists"
        ),
    )
    evidence_coverage = SufficiencyDimension(
        status=(
            DimensionStatus.PASS
            if supported
            else DimensionStatus.PARTIAL
            if scoped_evidence
            else DimensionStatus.FAIL
        ),
        evidence_refs=tuple(span.span_id for span in supported or scoped_evidence),
        reason=(
            "verified supporting evidence exists"
            if supported
            else "evidence is extracted but not support-verified"
            if scoped_evidence
            else "no cutoff-eligible evidence exists"
        ),
    )
    comparability = _signal_dimension(
        signals.comparison_context_confirmed,
        pass_reason="comparison conditions are confirmed",
        fail_reason="comparison conditions conflict",
        unknown_reason="comparison conditions have not been confirmed",
    )
    counterevidence = SufficiencyDimension(
        status=DimensionStatus.PASS if signals.counterevidence_checked else DimensionStatus.UNKNOWN,
        reason=(
            "counterevidence pass is recorded"
            if signals.counterevidence_checked
            else "counterevidence has not been checked"
        ),
    )
    instrumentation = _signal_dimension(
        signals.instrumentation_verified,
        pass_reason="measurement implementation is verified",
        fail_reason="measurement implementation failed verification",
        unknown_reason="measurement implementation is not verified",
    )
    expert_semantics = SufficiencyDimension(
        status=(
            DimensionStatus.PASS
            if signals.expert_semantics_confirmed and complete_criteria
            else DimensionStatus.PARTIAL
            if criteria
            else DimensionStatus.UNKNOWN
        ),
        evidence_refs=tuple(
            reference for criterion in complete_criteria for reference in criterion.evidence_refs
        ),
        reason=(
            "expert-confirmed semantics are bound to an official criterion"
            if signals.expert_semantics_confirmed and complete_criteria
            else "criterion semantics still require expert confirmation"
            if criteria
            else "no criterion semantics are available"
        ),
    )

    dimensions = (
        scope,
        criterion_authority,
        evidence_coverage,
        comparability,
        counterevidence,
        instrumentation,
        expert_semantics,
    )
    derived: list[SufficiencyStatus] = []
    if all(dimension.status == DimensionStatus.PASS for dimension in dimensions):
        derived.append(SufficiencyStatus.READY_FOR_MECHANICAL_EVALUATION)
    if scope.status == DimensionStatus.PASS and evidence_coverage.status != DimensionStatus.FAIL:
        derived.append(SufficiencyStatus.READY_TO_GENERATE_HYPOTHESES)
    if (
        evidence_coverage.status != DimensionStatus.PASS
        or counterevidence.status != DimensionStatus.PASS
    ):
        derived.append(SufficiencyStatus.EVIDENCE_ACQUISITION_REQUIRED)
    if (
        criterion_authority.status != DimensionStatus.PASS
        or expert_semantics.status != DimensionStatus.PASS
    ):
        derived.append(SufficiencyStatus.EXPERT_INPUT_REQUIRED)
    if comparability.status == DimensionStatus.FAIL:
        derived.append(SufficiencyStatus.INCOMPARABLE)
    if (
        evidence_coverage.status == DimensionStatus.FAIL
        and criterion_authority.status == DimensionStatus.FAIL
    ):
        derived.append(SufficiencyStatus.ABSTAIN)

    missing_items = _missing_items(
        criterion_authority,
        evidence_coverage,
        comparability,
        counterevidence,
        instrumentation,
        expert_semantics,
    )
    return InformationSufficiencyAssessment(
        assessment_id=assessment_id,
        assessment_revision_id=assessment_revision_id,
        project_id=project_id,
        target_object_id=target_object_id,
        cutoff_at=cutoff_at,
        decision_question=decision_question,
        scope_identity=scope,
        criterion_authority=criterion_authority,
        evidence_coverage=evidence_coverage,
        comparability=comparability,
        counterevidence=counterevidence,
        instrumentation=instrumentation,
        expert_semantics=expert_semantics,
        derived_status=tuple(derived),
        missing_items=missing_items,
        next_queries=tuple(f"Find or confirm: {item}" for item in missing_items),
        policy_version=policy_version,
        input_head_set_digest=input_head_set_digest,
    )


def _signal_dimension(
    value: bool | None,
    *,
    pass_reason: str,
    fail_reason: str,
    unknown_reason: str,
) -> SufficiencyDimension:
    if value is True:
        return SufficiencyDimension(status=DimensionStatus.PASS, reason=pass_reason)
    if value is False:
        return SufficiencyDimension(status=DimensionStatus.FAIL, reason=fail_reason)
    return SufficiencyDimension(status=DimensionStatus.UNKNOWN, reason=unknown_reason)


def _missing_items(*dimensions: SufficiencyDimension) -> tuple[str, ...]:
    return tuple(
        dimension.reason for dimension in dimensions if dimension.status != DimensionStatus.PASS
    )
