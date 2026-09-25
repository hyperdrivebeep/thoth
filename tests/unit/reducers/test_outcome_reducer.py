from __future__ import annotations

from decimal import Decimal

from thoth.application.reducers import rank_hypotheses
from thoth.domain.artifact import SourceLocator
from thoth.domain.enums import (
    AuthorityState,
    CausalDepth,
    CausalLocus,
    CutoffState,
    EvidenceEffect,
    HypothesisStatus,
    PortfolioStatus,
    Reversibility,
    RiskTier,
    SupportState,
    VerificationState,
)
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.hypothesis import DiscriminatingTest, Hypothesis, HypothesisPortfolio
from thoth.domain.outcome import HypothesisEvidenceUpdate

SHA = "a" * 64


def _hypothesis(identifier: str, locus: CausalLocus) -> Hypothesis:
    return Hypothesis(
        hypothesis_id=identifier,
        object_id="object:1",
        statement=f"{locus.value} explains the mismatch",
        observed_problem="result differs from target",
        primary_locus=locus,
        causal_depth=CausalDepth.INTERMEDIATE,
        scope_conditions={},
        support_evidence_refs=("span:1",),
        counterevidence_refs=(),
        counterevidence_queries=("search contrary runs",),
        assumptions=("run IDs are valid",),
        uncertainty="cause is not isolated",
        predicted_observations=("a discriminating run changes the mismatch",),
        discriminating_tests=(
            DiscriminatingTest(
                test_id=f"test:{identifier}",
                procedure_candidate="compare aligned runs",
                expected_if_true="mismatch narrows",
                expected_if_alternative="mismatch remains",
                risk_tier=RiskTier.R1,
                reversibility=Reversibility.FULL,
            ),
        ),
        status=HypothesisStatus.TESTABLE,
    )


def _evidence(identifier: str) -> EvidenceSpan:
    return EvidenceSpan(
        span_id=identifier,
        project_id="project:1",
        artifact_id="artifact:1",
        source_version_id="source-version:1",
        locator=SourceLocator(page=1),
        exact_text=identifier,
        text_sha256=("b" if identifier == "span:1" else "c") * 64,
        extraction_method="fixture",
        support_state=SupportState.SUPPORTED,
        authority_state=AuthorityState.OFFICIAL,
        verification_state=VerificationState.PROVENANCE_VALID,
        cutoff_state=CutoffState.ELIGIBLE,
    )


def test_new_evidence_changes_ranking_and_repeat_records_no_material_change() -> None:
    portfolio = HypothesisPortfolio(
        portfolio_id="portfolio:1",
        object_id="object:1",
        hypotheses=(
            _hypothesis("hypothesis:1", CausalLocus.MEASUREMENT_OBSERVATION),
            _hypothesis("hypothesis:2", CausalLocus.METHOD_DESIGN_IMPLEMENTATION),
        ),
        status=PortfolioStatus.TESTABLE,
        generated_from_head_set=SHA,
    )
    evidence = (_evidence("span:1"), _evidence("span:2"))
    initial = rank_hypotheses(
        portfolio=portfolio,
        evidence=evidence,
        updates=(
            HypothesisEvidenceUpdate(
                hypothesis_id="hypothesis:1",
                evidence_span_id="span:1",
                effect=EvidenceEffect.SUPPORT,
                weight=Decimal("1"),
                reason="baseline evidence",
            ),
        ),
        previous=None,
        input_head_set_digest=SHA,
    )
    updated = rank_hypotheses(
        portfolio=portfolio,
        evidence=evidence,
        updates=(
            HypothesisEvidenceUpdate(
                hypothesis_id="hypothesis:2",
                evidence_span_id="span:2",
                effect=EvidenceEffect.SUPPORT,
                weight=Decimal("2"),
                reason="new aligned run",
            ),
        ),
        previous=initial,
        input_head_set_digest="d" * 64,
    )
    repeated = rank_hypotheses(
        portfolio=portfolio,
        evidence=evidence,
        updates=(
            HypothesisEvidenceUpdate(
                hypothesis_id="hypothesis:2",
                evidence_span_id="span:2",
                effect=EvidenceEffect.SUPPORT,
                weight=Decimal("2"),
                reason="same aligned run",
            ),
        ),
        previous=updated,
        input_head_set_digest="d" * 64,
    )

    assert tuple(item.hypothesis_id for item in initial.ordered_scores) == (
        "hypothesis:1",
        "hypothesis:2",
    )
    assert tuple(item.hypothesis_id for item in updated.ordered_scores) == (
        "hypothesis:2",
        "hypothesis:1",
    )
    assert updated.material_change is True
    assert repeated.material_change is False
    assert repeated.reason == "new evidence produced no material ranking change"
