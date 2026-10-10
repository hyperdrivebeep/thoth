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
    # What the table-filling call did (see hypothesis_table_filler); never part of the form the
    # model fills in.
    _table_fill: Any = PrivateAttr(default=None)

    @property
    def table_fill(self) -> TableFillRecord | None:
        return self._table_fill

    def note_table_fill(self, record: TableFillRecord) -> None:
        self._table_fill = record


class FilledRow(DomainModel):
    """One cell the table filler answers. The limits are looser than ExpectedResult's: the server
    settles what is too long or unknown instead of refusing the whole answer."""

    hypothesis_id: HypothesisId
    expected: str = Field(min_length=1, max_length=300)
    basis: tuple[EvidenceSpanId, ...] = Field(default=(), max_length=20)
    # Only for 모름: one sentence saying under which condition this test's result is left
    # undetermined even if the hypothesis were true. Kept in the filling record, not in the table.
    unknown_reason: str | None = Field(default=None, max_length=2000)


class FilledTest(DomainModel):
    """The cells the filler answers for one test (named by the hypothesis it was designed for)."""

    designed_for: HypothesisId
    test_id: str = Field(min_length=1, max_length=300)
    rows: tuple[FilledRow, ...] = Field(default=(), max_length=40)


class HypothesisTableFill(DomainModel):
    """The output of the table-filling call: cells only, never a new test or hypothesis."""

    tests: tuple[FilledTest, ...] = Field(default=(), max_length=60)


TableFillState = Literal["CALLED", "SKIPPED_FULL", "SKIPPED_BUDGET", "FAILED"]
TableFillOutcome = Literal["CHANGED", "STILL_UNKNOWN", "NOT_ANSWERED", "DROPPED"]


class TableFillAnswer(DomainModel):
    """What the filler said for one cell it was asked about (cells it was not asked are absent).

    CHANGED: a value replaced a missing or 모름 cell. STILL_UNKNOWN: it answered 모름 (with its
    reason). NOT_ANSWERED: it gave no row for the cell. DROPPED: its answer was not kept.
    """

    test_id: str = Field(max_length=300)
    hypothesis_id: HypothesisId
    expected: str = Field(default="", max_length=80)
    unknown_reason: str | None = Field(default=None, max_length=200)
    outcome: TableFillOutcome


class TableFillRecord(DomainModel):
    """Whether the table-filling call was made, and what it changed (kept beside the portfolio).

    A cell is a (test, hypothesis) pair; it is open when its row is missing or says 모름.
    """

    state: TableFillState
    reason_code: str | None = Field(default=None, max_length=200)
    cells: int = Field(default=0, ge=0)
    open_before: int = Field(default=0, ge=0)
    open_after: int = Field(default=0, ge=0)
    changed_cells: int = Field(default=0, ge=0)
    answers: tuple[TableFillAnswer, ...] = ()


def is_v3(portfolio: HypothesisPortfolio) -> bool:
    """Whether this portfolio is the v3 form (the one a v3 generator call produced)."""
    return isinstance(portfolio, HypothesisPortfolioV3)


def portfolio_from_json(value: dict[str, Any], *, v3: bool) -> HypothesisPortfolio:
    """A portfolio from its JSON, in the form the caller says it has (v2 or v3)."""
    return (HypothesisPortfolioV3 if v3 else HypothesisPortfolio).model_validate(value)
