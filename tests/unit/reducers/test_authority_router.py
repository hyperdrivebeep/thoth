from __future__ import annotations

from datetime import UTC, datetime

import pytest

from thoth.application.reducers import ActionRiskFacts, classify_authority
from thoth.application.services import prepare_protected_action
from thoth.domain.action import ActionCandidate
from thoth.domain.enums import (
    ActionState,
    ExecutionAuthority,
    Reversibility,
    RiskTier,
)
from thoth.domain.errors import InvariantViolation


@pytest.mark.parametrize(
    ("facts", "tier", "authority"),
    [
        (ActionRiskFacts(), RiskTier.R0, ExecutionAuthority.AUTO_R0),
        (
            ActionRiskFacts(changes_local_draft=True),
            RiskTier.R1,
            ExecutionAuthority.PREAUTHORIZED_R1,
        ),
        (
            ActionRiskFacts(runs_untrusted_code=True),
            RiskTier.R2,
            ExecutionAuthority.SANDBOX_ONLY_R2,
        ),
        (
            ActionRiskFacts(external_write=True),
            RiskTier.R3,
            ExecutionAuthority.HUMAN_REQUIRED_R3,
        ),
        (
            ActionRiskFacts(changes_official_kpi=True),
            RiskTier.R4,
            ExecutionAuthority.PROHIBITED_R4,
        ),
    ],
)
def test_authority_router_priority(
    facts: ActionRiskFacts,
    tier: RiskTier,
    authority: ExecutionAuthority,
) -> None:
    route = classify_authority(facts)

    assert route.risk_tier == tier
    assert route.execution_authority == authority


def test_r3_only_prepares_single_use_card_and_has_no_execution_result() -> None:
    action = ActionCandidate(
        action_id="action:r3",
        object_id="object:1",
        hypothesis_ids=("hypothesis:1",),
        action_family="PHYSICAL_TEST",
        specification="run an instrumented field test",
        expected_information_value="separates environment and implementation causes",
        risk_tier=RiskTier.R3,
        execution_authority=ExecutionAuthority.HUMAN_REQUIRED_R3,
        reversibility=Reversibility.PARTIAL,
        external_write=True,
        sandbox_required=False,
        state=ActionState.APPROVAL_PENDING,
        required_approver_role="test-owner",
        source_refs=("span:1",),
    )

    card = prepare_protected_action(
        action,
        plan_revision_digest="a" * 64,
        step_id="step:field-test",
        exact_input_digests=("b" * 64,),
        target_revision="c" * 64,
        baseline_revision="d" * 64,
        tool="test-rig",
        environment="approved-field-site",
        egress="none",
        budget="one run",
        time_limit_seconds=3600,
        stop_conditions=("sensor safety alarm",),
        compensation="restore previous calibration",
        required_roles=("test-owner",),
        expires_at=datetime(2026, 8, 31, tzinfo=UTC),
    )

    assert card.single_use is True
    assert not hasattr(card, "execution_result")


def test_non_r3_action_cannot_receive_protected_card() -> None:
    action = ActionCandidate(
        action_id="action:r0",
        object_id="object:1",
        hypothesis_ids=("hypothesis:1",),
        action_family="READ_ONLY_ANALYSIS",
        specification="compare two stored runs",
        expected_information_value="detects a configuration mismatch",
        risk_tier=RiskTier.R0,
        execution_authority=ExecutionAuthority.AUTO_R0,
        reversibility=Reversibility.FULL,
        external_write=False,
        sandbox_required=False,
        source_refs=("span:1",),
    )

    with pytest.raises(InvariantViolation, match="only for R3"):
        prepare_protected_action(
            action,
            plan_revision_digest="a" * 64,
            step_id="step:read",
            exact_input_digests=("b" * 64,),
            target_revision="c" * 64,
            baseline_revision=None,
            tool="reader",
            environment="local",
            egress="none",
            budget="none",
            time_limit_seconds=30,
            stop_conditions=("timeout",),
            compensation="none",
            required_roles=("researcher",),
            expires_at=datetime(2026, 8, 31, tzinfo=UTC),
        )
