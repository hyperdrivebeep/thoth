from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from thoth.domain.action import ActionCandidate
from thoth.domain.actor import ActorRef
from thoth.domain.criterion import CriterionCandidate
from thoth.domain.enums import (
    ActionState,
    ActorKind,
    AuthorityState,
    CausalDepth,
    CausalLocus,
    ExecutionAuthority,
    HypothesisStatus,
    IntegrityState,
    PortfolioStatus,
    ProvenanceState,
    ReceiptClaimScope,
    ReceiptType,
    Reversibility,
    RiskTier,
    SignatureState,
    TimestampTrust,
)
from thoth.domain.hypothesis import DiscriminatingTest, Hypothesis, HypothesisPortfolio
from thoth.domain.receipt import Receipt

SHA = "a" * 64


def _hypothesis(identifier: str, locus: CausalLocus) -> Hypothesis:
    return Hypothesis(
        hypothesis_id=identifier,
        object_id="object:1",
        statement=f"{locus.value} may explain the scoped observation.",
        observed_problem="reported and expected results differ",
        primary_locus=locus,
        causal_depth=CausalDepth.INTERMEDIATE,
        scope_conditions={"dataset": "v1"},
        support_evidence_refs=("span:1",),
        counterevidence_refs=(),
        assumptions=("source locator is valid",),
        uncertainty="mechanism not yet isolated",
        predicted_observations=("the mismatch changes under the discriminating condition",),
        discriminating_tests=(
            DiscriminatingTest(
                test_id=f"test:{identifier}",
                procedure_candidate="compare aligned and unaligned configurations",
                expected_if_true="difference narrows after alignment",
                expected_if_alternative="difference remains",
                risk_tier=RiskTier.R1,
                reversibility=Reversibility.FULL,
            ),
        ),
        status=HypothesisStatus.PREDICTION_BOUND,
    )


def test_portfolio_does_not_confuse_locus_count_with_semantic_diversity() -> None:
    first = _hypothesis("hypothesis:1", CausalLocus.MEASUREMENT_OBSERVATION)
    second = _hypothesis("hypothesis:2", CausalLocus.MEASUREMENT_OBSERVATION)
    portfolio = HypothesisPortfolio(
        portfolio_id="portfolio:1", object_id="object:1", hypotheses=(first, second),
        status=PortfolioStatus.DRAFT, generated_from_head_set=SHA)
    assert portfolio.status == PortfolioStatus.DRAFT
    assert portfolio.uncertainty_reserve == "UNASSESSED"
    with pytest.raises(ValidationError, match="0/1 portfolio"):
        HypothesisPortfolio(portfolio_id="portfolio:1", object_id="object:1", hypotheses=(first,),
            status=PortfolioStatus.DRAFT, generated_from_head_set=SHA)


def test_hypothesis_portfolio_accepts_distinct_loci() -> None:
    portfolio = HypothesisPortfolio(
        portfolio_id="portfolio:1",
        object_id="object:1",
        hypotheses=(
            _hypothesis("hypothesis:1", CausalLocus.MEASUREMENT_OBSERVATION),
            _hypothesis("hypothesis:2", CausalLocus.COMPONENT_INTERFACE_SYSTEM),
        ),
        status=PortfolioStatus.GROUNDED,
        generated_from_head_set=SHA,
    )
    assert len(portfolio.hypotheses) == 2


def test_reference_criterion_cannot_be_evaluator_input() -> None:
    with pytest.raises(ValidationError, match="reference candidate"):
        CriterionCandidate(
            criterion_id="criterion:1",
            project_id="project:1",
            name="candidate target",
            measured_construct="accuracy",
            context={},
            evidence_refs=("span:1",),
            authority_state=AuthorityState.INFORMAL,
            reference_candidate=True,
            evaluator_input_allowed=True,
        )


@pytest.mark.parametrize(
    ("tier", "authority", "sandbox", "external", "approver", "state"),
    [
        (RiskTier.R0, ExecutionAuthority.AUTO_R0, False, False, None, ActionState.PROPOSED),
        (
            RiskTier.R1,
            ExecutionAuthority.PREAUTHORIZED_R1,
            False,
            False,
            None,
            ActionState.PROPOSED,
        ),
        (
            RiskTier.R2,
            ExecutionAuthority.SANDBOX_ONLY_R2,
            True,
            False,
            None,
            ActionState.PROPOSED,
        ),
        (
            RiskTier.R3,
            ExecutionAuthority.HUMAN_REQUIRED_R3,
            False,
            True,
            "decision-owner",
            ActionState.APPROVAL_PENDING,
        ),
        (
            RiskTier.R4,
            ExecutionAuthority.PROHIBITED_R4,
            False,
            True,
            None,
            ActionState.PROHIBITED,
        ),
    ],
)
def test_action_authority_invariants_accept_valid_combinations(
    tier: RiskTier,
    authority: ExecutionAuthority,
    sandbox: bool,
    external: bool,
    approver: str | None,
    state: ActionState,
) -> None:
    action = ActionCandidate(
        action_id="action:1",
        object_id="object:1",
        hypothesis_ids=("hypothesis:1",),
        action_family="ANALYSIS",
        specification="perform the bounded check",
        expected_information_value="separates two alternatives",
        risk_tier=tier,
        execution_authority=authority,
        reversibility=Reversibility.FULL if tier != RiskTier.R4 else Reversibility.NONE,
        external_write=external,
        sandbox_required=sandbox,
        state=state,
        required_approver_role=approver,
        source_refs=("span:1",),
    )
    assert action.risk_tier == tier


def test_r3_requires_approver() -> None:
    with pytest.raises(ValidationError, match="approver role"):
        ActionCandidate(
            action_id="action:1",
            object_id="object:1",
            hypothesis_ids=(),
            action_family="PHYSICAL_TEST",
            specification="perform a physical test",
            expected_information_value="validates the system",
            risk_tier=RiskTier.R3,
            execution_authority=ExecutionAuthority.HUMAN_REQUIRED_R3,
            reversibility=Reversibility.NONE,
            external_write=True,
            sandbox_required=False,
            state=ActionState.APPROVAL_PENDING,
            source_refs=("span:1",),
        )


def test_receipt_never_certifies_semantic_truth() -> None:
    receipt = Receipt(
        receipt_id="receipt:1",
        project_id="project:1",
        receipt_type=ReceiptType.TRANSITION,
        claim_scopes=(ReceiptClaimScope.TRANSITION_RECORDED,),
        subject_refs=("revision:1",),
        before_head_set_digest=SHA,
        after_head_set_digest="b" * 64,
        policy_version="policy:1",
        integrity_state=IntegrityState.VALID,
        provenance_state=ProvenanceState.COMPLETE,
        signature_state=SignatureState.NOT_PRESENT,
        timestamp_trust=TimestampTrust.LOCAL_ONLY,
        recorded_at=datetime(2026, 8, 30, tzinfo=UTC),
        receipt_digest="c" * 64,
    )
    assert receipt.semantic_truth_certified is False


def test_actor_ref_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        ActorRef(
            actor_id="agent:1",
            kind=ActorKind.AGENT,
            role="hypothesis-generator",
            unexpected="not allowed",  # type: ignore[call-arg]
        )
