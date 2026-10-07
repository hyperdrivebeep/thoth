from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import Field, PrivateAttr, SerializeAsAny, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.counterevidence import HypothesisCriticalReview
from thoth.domain.enums import (
    CausalDepth,
    CausalLocus,
    HypothesisStatus,
    PortfolioStatus,
    Reversibility,
    RiskTier,
)
from thoth.domain.ids import DecisionObjectId, EvidenceSpanId, HypothesisId, Sha256
from thoth.domain.r2_loop import HypothesisExecutionAppraisal
from thoth.domain.test_validity import PredictionProposal


class DiscriminatingTest(DomainModel):
    test_id: str
    procedure_candidate: str
    expected_if_true: str
    expected_if_alternative: str
    risk_tier: RiskTier
    reversibility: Reversibility
    estimated_cost: Decimal | None = Field(default=None, ge=0)


class Hypothesis(DomainModel):
    hypothesis_id: HypothesisId
    object_id: DecisionObjectId
    statement: str
    observed_problem: str
    primary_locus: CausalLocus | None
    contributing_loci: tuple[CausalLocus, ...] = ()
    causal_depth: CausalDepth
    scope_conditions: dict[str, str]
    support_evidence_refs: tuple[EvidenceSpanId, ...]
    counterevidence_refs: tuple[EvidenceSpanId, ...]
    missing_evidence: tuple[str, ...] = ()
    counterevidence_queries: tuple[str, ...] = ()
    assumptions: tuple[str, ...]
    uncertainty: str
    predicted_observations: tuple[str, ...]
    # Dumped as what they are, so a v3 test keeps its extra fields wherever the portfolio is dumped.
    discriminating_tests: tuple[SerializeAsAny[DiscriminatingTest], ...]
    status: HypothesisStatus
    primary_intent: (
        Literal[
            "DIAGNOSTIC_CAUSAL",
            "EXPLANATORY_MECHANISTIC",
            "PREDICTIVE",
            "INTERVENTION_DESIGN",
            "EXPLORATORY",
        ]
        | None
    ) = None
    critical_review: HypothesisCriticalReview | None = None
    execution_appraisal: HypothesisExecutionAppraisal | None = None
    prediction_proposal: PredictionProposal | None = None
    semantic_review_ref: str | None = None
    schema_version: str = "1.0.0"

    @model_validator(mode="after")
    def require_testable_content(self) -> Hypothesis:
        if not self.support_evidence_refs and not self.missing_evidence:
            raise ValueError("hypothesis requires support evidence or an explicit evidence gap")
        if self.status != HypothesisStatus.DRAFT and not self.predicted_observations:
            raise ValueError("hypothesis requires at least one predicted observation")
        if self.status != HypothesisStatus.DRAFT and not self.discriminating_tests:
            raise ValueError("hypothesis requires at least one discriminating test")
        if (
            self.primary_locus is None
            and self.primary_intent
            in {"DIAGNOSTIC_CAUSAL", "EXPLANATORY_MECHANISTIC", "INTERVENTION_DESIGN"}
            and self.status != HypothesisStatus.DRAFT
        ):
            raise ValueError("causal promotion requires a causal locus")
        return self


class HypothesisPortfolio(DomainModel):
    portfolio_id: str
    object_id: DecisionObjectId
    hypotheses: tuple[SerializeAsAny[Hypothesis], ...]
    status: PortfolioStatus
    generated_from_head_set: Sha256
    alternatives_considered: tuple[str, ...] = ()
    next_checks: tuple[str, ...] = ()
    uncertainty_reserve: str = "UNASSESSED"
    schema_version: str = "1.0.0"

    @model_validator(mode="after")
    def require_material_alternatives(self) -> HypothesisPortfolio:
        if len(self.hypotheses) < 2 and (
            not self.alternatives_considered
            or not self.next_checks
            or self.uncertainty_reserve == "UNASSESSED"
        ):
            raise ValueError(
                "0/1 portfolio requires alternative review, next checks and uncertainty reserve"
            )
        if len({h.hypothesis_id for h in self.hypotheses}) != len(self.hypotheses):
            raise ValueError("duplicate hypothesis ID")
        return self


class ExpectedResult(DomainModel):
    """What one hypothesis expects from a test, as a short label: the same label is the same result.

    "모름" says the generator does not know; no probability or score exists here.
    """

    hypothesis_id: HypothesisId
    expected: str = Field(min_length=1, max_length=80)
    basis: tuple[EvidenceSpanId, ...] = Field(default=(), max_length=5)


class DiscriminatingTestV3(DiscriminatingTest):
    expected_by_hypothesis: tuple[ExpectedResult, ...] = Field(default=(), max_length=10)


class HypothesisV3(Hypothesis):
    discriminating_tests: tuple[SerializeAsAny[DiscriminatingTestV3], ...]  # pyright: ignore[reportIncompatibleVariableOverride]
    refutation_conditions: tuple[Annotated[str, Field(min_length=1, max_length=300)], ...] = Field(
        default=(), max_length=5
    )
    # What settling the generator's output dropped (see hypothesis_contract_v3); never part of the
    # form the model fills in.
    _dropped: tuple[Any, ...] = PrivateAttr(default=())

    @property
    def dropped(self) -> tuple[Any, ...]:
        return self._dropped

    def note_dropped(self, items: tuple[Any, ...]) -> None:
        self._dropped = items


class HypothesisPortfolioV3(HypothesisPortfolio):
    """The v3 output form: a v2 portfolio whose hypotheses may add expected results."""

    hypotheses: tuple[SerializeAsAny[HypothesisV3], ...]  # pyright: ignore[reportIncompatibleVariableOverride]


def is_v3(portfolio: HypothesisPortfolio) -> bool:
    """Whether this portfolio is the v3 form (the one a v3 generator call produced)."""
    return isinstance(portfolio, HypothesisPortfolioV3)


def portfolio_from_json(value: dict[str, Any], *, v3: bool) -> HypothesisPortfolio:
    """A portfolio from its JSON, in the form the caller says it has (v2 or v3)."""
    return (HypothesisPortfolioV3 if v3 else HypothesisPortfolio).model_validate(value)
