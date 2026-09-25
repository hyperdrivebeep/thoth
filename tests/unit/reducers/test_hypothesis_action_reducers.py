from __future__ import annotations

from thoth.application.reducers import (
    ActionRiskFacts,
    validate_action_plan,
    validate_hypothesis_portfolio,
)
from thoth.domain.action import (
    ActionCandidate,
    ActionPlan,
    DecisionAnalysis,
    DecisionCriterion,
)
from thoth.domain.artifact import SourceLocator
from thoth.domain.enums import (
    ActionState,
    AuthorityState,
    CausalDepth,
    CausalLocus,
    CutoffState,
    ExecutionAuthority,
    HypothesisStatus,
    PortfolioStatus,
    Reversibility,
    RiskTier,
    SupportState,
    VerificationState,
)
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.hypothesis import DiscriminatingTest, Hypothesis, HypothesisPortfolio

SHA = "a" * 64


def _evidence() -> EvidenceSpan:
    return EvidenceSpan(
        span_id="span:1",
        project_id="project:1",
        artifact_id="artifact:1",
        source_version_id="source-version:1",
        locator=SourceLocator(page=1),
        exact_text="야간 정확도는 88%였다.",
        text_sha256="b" * 64,
        extraction_method="pdf:1",
        support_state=SupportState.SUPPORTED,
        authority_state=AuthorityState.OFFICIAL,
        verification_state=VerificationState.PROVENANCE_VALID,
        cutoff_state=CutoffState.ELIGIBLE,
    )


def _hypothesis(identifier: str, locus: CausalLocus) -> Hypothesis:
    return Hypothesis(
        hypothesis_id=identifier,
        object_id="object:1",
        statement=f"{locus.value} mismatch may explain the result",
        observed_problem="accuracy is below target",
        primary_locus=locus,
        causal_depth=CausalDepth.INTERMEDIATE,
        scope_conditions={"condition": "night-rain"},
        support_evidence_refs=("span:1",),
        counterevidence_refs=(),
        counterevidence_queries=(f"find evidence against {locus.value}",),
        assumptions=("run identity is correct",),
        uncertainty="mechanism is not isolated",
        predicted_observations=("aligned configuration changes the error",),
        discriminating_tests=(
            DiscriminatingTest(
                test_id=f"test:{identifier}",
                procedure_candidate="compare aligned and current configurations",
                expected_if_true="error narrows",
                expected_if_alternative="error remains",
                risk_tier=RiskTier.R1,
                reversibility=Reversibility.FULL,
            ),
        ),
        status=HypothesisStatus.PREDICTION_BOUND,
    )


def _portfolio() -> HypothesisPortfolio:
    return HypothesisPortfolio(
        portfolio_id="portfolio:1",
        object_id="object:1",
        hypotheses=(
            _hypothesis("hypothesis:measurement", CausalLocus.MEASUREMENT_OBSERVATION),
            _hypothesis("hypothesis:method", CausalLocus.METHOD_DESIGN_IMPLEMENTATION),
            _hypothesis("hypothesis:unknown", CausalLocus.OTHER_WITH_DESCRIPTION),
        ),
        status=PortfolioStatus.TESTABLE,
        generated_from_head_set=SHA,
    )


def _action(
    identifier: str,
    family: str,
    tier: RiskTier,
    authority: ExecutionAuthority,
    *,
    sandbox: bool = False,
    external: bool = False,
    approver: str | None = None,
) -> ActionCandidate:
    return ActionCandidate(
        action_id=identifier,
        object_id="object:1",
        hypothesis_ids=("hypothesis:measurement",),
        action_family=family,
        specification="perform the bounded comparison",
        expected_information_value="distinguishes two hypotheses",
        risk_tier=tier,
        execution_authority=authority,
        reversibility=Reversibility.FULL,
        external_write=external,
        sandbox_required=sandbox,
        state=ActionState.APPROVAL_PENDING if tier == RiskTier.R3 else ActionState.PROPOSED,
        required_approver_role=approver,
        source_refs=("span:1",),
    )


def test_portfolio_and_action_plan_are_source_grounded_and_authority_checked() -> None:
    evidence = (_evidence(),)
    portfolio = validate_hypothesis_portfolio(
        _portfolio(), evidence=evidence, require_unknown_alternative=True
    )
    actions = (
        _action("action:read", "READ_ONLY_ANALYSIS", RiskTier.R0, ExecutionAuthority.AUTO_R0),
        _action(
            "action:sandbox",
            "SANDBOX_REPLAY",
            RiskTier.R2,
            ExecutionAuthority.SANDBOX_ONLY_R2,
            sandbox=True,
        ),
        _action(
            "action:field",
            "PHYSICAL_TEST",
            RiskTier.R3,
            ExecutionAuthority.HUMAN_REQUIRED_R3,
            external=True,
            approver="test-owner",
        ),
    )
    plan = ActionPlan(
        plan_id="plan:1",
        object_id="object:1",
        alternatives=actions,
        decision_analysis=DecisionAnalysis(
            decision="choose the next information-gaining check",
            criteria=(
                DecisionCriterion(
                    criterion_id="decision-criterion:1",
                    name="information gain",
                    mandatory=True,
                    rationale="separate competing causes",
                ),
            ),
            evaluations=(),
            uncertainty="field conditions remain uncertain",
            sensitivity="ranking changes if field access is unavailable",
        ),
        frontier=("action:read", "action:sandbox"),
        plan_revision_digest=SHA,
    )

    validated = validate_action_plan(
        plan,
        portfolio=portfolio,
        evidence=evidence,
        risk_facts={
            "action:read": ActionRiskFacts(),
            "action:sandbox": ActionRiskFacts(runs_untrusted_code=True),
            "action:field": ActionRiskFacts(external_write=True),
        },
    )

    assert validated.frontier == ("action:read", "action:sandbox")
