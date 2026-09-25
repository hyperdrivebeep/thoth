from __future__ import annotations

from decimal import Decimal

from thoth.application.services import compile_criterion
from thoth.domain.artifact import SourceLocator
from thoth.domain.criterion import (
    CriterionCompilationSignals,
    CriterionDraft,
    FormulaComputation,
    ThresholdRule,
)
from thoth.domain.enums import (
    AuthorityState,
    CriterionCompilationStatus,
    CriterionOrigin,
    CutoffState,
    SupportState,
    VerificationState,
)
from thoth.domain.evidence import EvidenceSpan


def _evidence(identifier: str = "span:criterion") -> EvidenceSpan:
    return EvidenceSpan(
        span_id=identifier,
        project_id="project:criterion",
        artifact_id="artifact:plan",
        source_version_id="source-version:plan",
        locator=SourceLocator(page=12),
        exact_text="User plane latency shall be below 1 ms under the declared baseline.",
        text_sha256="a" * 64,
        extraction_method="pdf:1.0.0",
        support_state=SupportState.EXTRACTED,
        authority_state=AuthorityState.OFFICIAL,
        verification_state=VerificationState.PROVENANCE_VALID,
        cutoff_state=CutoffState.ELIGIBLE,
    )


def _draft(origin: CriterionOrigin = CriterionOrigin.DOCUMENT_EXTRACTED) -> CriterionDraft:
    refs = ("span:criterion",)
    return CriterionDraft(
        criterion_id="criterion:latency",
        project_id="project:criterion",
        name="User plane latency",
        measured_construct="round-trip latency",
        computation=FormulaComputation(expression="mean(samples_ms)", unit="ms"),
        context={"network": "declared baseline", "direction": "round-trip"},
        acceptance_rule=ThresholdRule(operator="<", target=Decimal("1")),
        origin=origin,
        field_evidence={
            "name": refs,
            "measured_construct": refs,
            "computation": refs,
            "acceptance_rule": refs,
            "context": refs,
        },
        uncertainty="sample aggregation semantics require confirmation",
    )


def test_official_extraction_stays_under_expert_review_until_all_signals_pass() -> None:
    result = compile_criterion(
        _draft(), evidence=(_evidence(),), signals=CriterionCompilationSignals()
    )

    assert result.status == CriterionCompilationStatus.EXPERT_REVIEW_REQUIRED
    assert result.candidate is not None
    assert result.candidate.authority_state == AuthorityState.OFFICIAL
    assert result.candidate.evaluator_input_allowed is False
    assert len(result.expert_questions) == 3


def test_complete_official_extraction_can_become_evaluator_input_after_independent_gates() -> None:
    result = compile_criterion(
        _draft(),
        evidence=(_evidence(),),
        signals=CriterionCompilationSignals(
            source_scope_confirmed=True,
            expert_semantics_confirmed=True,
            computation_verified=True,
        ),
    )

    assert result.status == CriterionCompilationStatus.EVALUATOR_READY
    assert result.candidate is not None
    assert result.candidate.evaluator_input_allowed is True
    assert result.candidate.reference_candidate is False


def test_reference_proposal_never_becomes_evaluator_input() -> None:
    result = compile_criterion(
        _draft(CriterionOrigin.REFERENCE_PROPOSAL),
        evidence=(_evidence(),),
        signals=CriterionCompilationSignals(
            source_scope_confirmed=True,
            expert_semantics_confirmed=True,
            computation_verified=True,
        ),
    )

    assert result.status == CriterionCompilationStatus.REFERENCE_ONLY
    assert result.candidate is not None
    assert result.candidate.reference_candidate is True
    assert result.candidate.evaluator_input_allowed is False


def test_unknown_evidence_reference_is_rejected() -> None:
    draft = _draft().model_copy(
        update={"field_evidence": {"name": ("span:missing",)}}
    )

    result = compile_criterion(
        draft, evidence=(_evidence(),), signals=CriterionCompilationSignals()
    )

    assert result.status == CriterionCompilationStatus.REJECTED
    assert result.candidate is None
    assert result.invalid_evidence_refs == ("span:missing",)
