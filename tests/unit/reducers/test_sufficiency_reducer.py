from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from thoth.application.reducers import SufficiencySignals, assess_information_sufficiency
from thoth.domain.artifact import SourceLocator
from thoth.domain.criterion import CriterionCandidate, FormulaComputation, ThresholdRule
from thoth.domain.enums import (
    AuthorityState,
    CutoffState,
    SufficiencyStatus,
    SupportState,
    VerificationState,
)
from thoth.domain.evidence import EvidenceSpan

SHA = "a" * 64


def _evidence(
    *,
    support: SupportState = SupportState.EXTRACTED,
    verification: VerificationState = VerificationState.SCHEMA_VALID,
) -> EvidenceSpan:
    return EvidenceSpan(
        span_id="span:1",
        project_id="project:test",
        artifact_id="artifact:1",
        source_version_id="source-version:1",
        locator=SourceLocator(page=1),
        exact_text="목표 정확도는 94% 이상이다.",
        text_sha256="b" * 64,
        extraction_method="pdf:1.0.0",
        support_state=support,
        authority_state=AuthorityState.OFFICIAL,
        verification_state=verification,
        cutoff_state=CutoffState.ELIGIBLE,
    )


def _criterion() -> CriterionCandidate:
    return CriterionCandidate(
        criterion_id="criterion:1",
        project_id="project:test",
        name="탐지 정확도",
        measured_construct="accuracy",
        computation=FormulaComputation(expression="correct / total * 100", unit="%"),
        context={"dataset": "v3", "condition": "night-rain"},
        acceptance_rule=ThresholdRule(operator=">=", target=Decimal("94")),
        evidence_refs=("span:1",),
        authority_state=AuthorityState.OFFICIAL,
        evaluator_input_allowed=True,
    )


def _assess(
    *,
    criteria: tuple[CriterionCandidate, ...],
    evidence: tuple[EvidenceSpan, ...],
    signals: SufficiencySignals,
):  # type: ignore[no-untyped-def]
    return assess_information_sufficiency(
        assessment_id="assessment:1",
        assessment_revision_id="revision:1",
        project_id="project:test",
        target_object_id="object:1",
        cutoff_at=datetime(2026, 8, 30, tzinfo=UTC),
        decision_question="왜 야간 강우 정확도가 목표에 미달했는가?",
        criteria=criteria,
        evidence=evidence,
        signals=signals,
        policy_version="policy:1",
        input_head_set_digest=SHA,
    )


def test_extracted_evidence_without_official_criterion_requires_more_evidence_and_expert() -> None:
    assessment = _assess(
        criteria=(),
        evidence=(_evidence(),),
        signals=SufficiencySignals(),
    )

    assert SufficiencyStatus.READY_TO_GENERATE_HYPOTHESES in assessment.derived_status
    assert SufficiencyStatus.EVIDENCE_ACQUISITION_REQUIRED in assessment.derived_status
    assert SufficiencyStatus.EXPERT_INPUT_REQUIRED in assessment.derived_status
    assert SufficiencyStatus.READY_FOR_MECHANICAL_EVALUATION not in assessment.derived_status


def test_all_independently_verified_dimensions_enable_mechanical_evaluation() -> None:
    assessment = _assess(
        criteria=(_criterion(),),
        evidence=(
            _evidence(
                support=SupportState.SUPPORTED,
                verification=VerificationState.DETERMINISTICALLY_VERIFIED,
            ),
        ),
        signals=SufficiencySignals(
            comparison_context_confirmed=True,
            counterevidence_checked=True,
            instrumentation_verified=True,
            expert_semantics_confirmed=True,
        ),
    )

    assert assessment.derived_status == (
        SufficiencyStatus.READY_FOR_MECHANICAL_EVALUATION,
        SufficiencyStatus.READY_TO_GENERATE_HYPOTHESES,
    )


def test_conflicting_comparison_conditions_produce_incomparable() -> None:
    assessment = _assess(
        criteria=(_criterion(),),
        evidence=(_evidence(),),
        signals=SufficiencySignals(comparison_context_confirmed=False),
    )

    assert SufficiencyStatus.INCOMPARABLE in assessment.derived_status
