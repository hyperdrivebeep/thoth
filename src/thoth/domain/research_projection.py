"""Typed candidate details retained without claiming sealed tests or canonical appraisal."""

from __future__ import annotations

from decimal import Decimal

from thoth.domain.action import ActionRiskFacts, DecisionAnalysis, OrdinalEstimate
from thoth.domain.base import DomainModel
from thoth.domain.counterevidence import HypothesisCriticalReview
from thoth.domain.enums import (
    ActionState,
    CausalDepth,
    CausalLocus,
    ExecutionAuthority,
    HypothesisStatus,
    PortfolioStatus,
    Reversibility,
)
from thoth.domain.hypothesis import DiscriminatingTest, ExpectedResult, TableFillRecord
from thoth.domain.r2_loop import HypothesisExecutionAppraisal, R2ExecutionSummary
from thoth.domain.recovery import RecoveryCandidateRevision
from thoth.domain.test_validity import PredictionProposal


class ExpectedByTest(DomainModel):
    """What each hypothesis was said to expect from one discriminating test (contract v3)."""

    test_id: str
    rows: tuple[ExpectedResult, ...]


class DroppedExpectation(DomainModel):
    """An item of the generator's output that was not kept, and why; the rest of the output stands.

    An expected-result row names its test (`test_id`); a refutation condition has no test, so its
    `test_id` is empty and its original `text` is kept so the drop can be audited.
    """

    test_id: str
    hypothesis_id: str
    reason: str
    text: str | None = None


class HypothesisGenerationDetails(DomainModel):
    primary_locus: CausalLocus | None
    contributing_loci: tuple[CausalLocus, ...]
    causal_depth: CausalDepth
    missing_evidence: tuple[str, ...]
    assumptions: tuple[str, ...]
    uncertainty: str
    predicted_observation_candidates: tuple[str, ...]
    discriminating_test_candidates: tuple[DiscriminatingTest, ...]
    candidate_status: HypothesisStatus
    critical_review: HypothesisCriticalReview | None = None
    execution_appraisal: HypothesisExecutionAppraisal | None = None
    prediction_proposal: PredictionProposal | None = None
    semantic_review_ref: str | None = None
    # Contract v3 only (absent means the v2 contract made it): the refutation conditions, what each
    # hypothesis expects from each test, what was dropped, and the contract version.
    refutation_conditions: tuple[str, ...] = ()
    expected_results: tuple[ExpectedByTest, ...] = ()
    dropped_expected_results: tuple[DroppedExpectation, ...] = ()
    contract_version: str | None = None


class PortfolioGenerationDetails(DomainModel):
    candidate_status: PortfolioStatus
    generated_from_head_set: str
    alternatives_considered: tuple[str, ...] = ()
    next_checks: tuple[str, ...] = ()
    uncertainty_reserve: str = "UNASSESSED"
    # Contract v3 only: whether the table-filling call was made and what it changed.
    table_fill: TableFillRecord | None = None


class ActionGenerationDetails(DomainModel):
    action_family: str
    estimated_cost: Decimal | None
    estimated_seconds: int | None
    execution_authority: ExecutionAuthority
    reversibility: Reversibility
    sandbox_required: bool
    candidate_state: ActionState
    required_approver_role: str | None
    missing_evidence: tuple[str, ...]
    effect_facts: ActionRiskFacts | None = None
    effect_completeness_confirmed: bool = False
    effort_estimates: tuple[OrdinalEstimate, ...] = ()


class ActionPlanGenerationDetails(DomainModel):
    portfolio_id: str
    alternative_refs: tuple[str, ...]
    decision_analysis: DecisionAnalysis
    generated_from_head_set: str
    next_action_order: tuple[str, ...] = ()
    last_r2_execution: R2ExecutionSummary | None = None
    recovery_candidate: RecoveryCandidateRevision | None = None
