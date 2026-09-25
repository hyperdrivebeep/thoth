from __future__ import annotations

import pytest

from thoth.application.services import compile_action, compile_action_plan
from thoth.domain.action import (
    ActionCompilationPolicy,
    ActionDraft,
    ActionPlanDraft,
    ActionRiskFacts,
    DecisionAnalysis,
    DecisionCriterion,
)
from thoth.domain.enums import (
    ActionCompilationStatus,
    ActionPlanCompilationStatus,
    ExecutionAuthority,
    Reversibility,
    RiskTier,
)


def _draft(
    *,
    family: str = "READ_ONLY_ANALYSIS",
    facts: ActionRiskFacts | None = None,
    complete: bool = True,
) -> ActionDraft:
    return ActionDraft(
        action_id=f"action:{family.lower()}",
        object_id="object:1",
        hypothesis_ids=("hypothesis:1",),
        action_family=family,
        specification="perform the bounded next check",
        expected_information_value="separates two competing explanations",
        reversibility=Reversibility.FULL,
        effect_facts=facts or ActionRiskFacts(),
        effect_completeness_confirmed=complete,
        source_refs=("span:1",),
    )


def _policy() -> ActionCompilationPolicy:
    return ActionCompilationPolicy(
        minimum_tier_by_family={
            "READ_ONLY_ANALYSIS": RiskTier.R0,
            "SANDBOX_REPLAY": RiskTier.R2,
            "CONTROLLED_RERUN": RiskTier.R3,
            "OFFICIAL_KPI_CHANGE": RiskTier.R4,
        },
        approver_role_by_family={
            "READ_ONLY_ANALYSIS": "project-owner",
            "CONTROLLED_RERUN": "test-owner",
        },
    )


@pytest.mark.parametrize(
    ("draft", "tier", "authority", "status"),
    [
        (
            _draft(),
            RiskTier.R0,
            ExecutionAuthority.AUTO_R0,
            ActionCompilationStatus.AUTO_CANDIDATE,
        ),
        (
            _draft(
                family="SANDBOX_REPLAY",
                facts=ActionRiskFacts(runs_untrusted_code=True),
            ),
            RiskTier.R2,
            ExecutionAuthority.SANDBOX_ONLY_R2,
            ActionCompilationStatus.AUTO_CANDIDATE,
        ),
        (
            _draft(
                family="CONTROLLED_RERUN",
                facts=ActionRiskFacts(physical_action=True),
            ),
            RiskTier.R3,
            ExecutionAuthority.HUMAN_REQUIRED_R3,
            ActionCompilationStatus.PROTECTED_ACTION,
        ),
        (
            _draft(
                family="OFFICIAL_KPI_CHANGE",
                facts=ActionRiskFacts(changes_official_kpi=True),
            ),
            RiskTier.R4,
            ExecutionAuthority.PROHIBITED_R4,
            ActionCompilationStatus.PROHIBITED,
        ),
    ],
)
def test_compiler_assigns_authority_from_effects_and_policy(
    draft: ActionDraft,
    tier: RiskTier,
    authority: ExecutionAuthority,
    status: ActionCompilationStatus,
) -> None:
    result = compile_action(
        draft,
        evidence_ids=frozenset({"span:1"}),
        hypothesis_ids=frozenset({"hypothesis:1"}),
        policy=_policy(),
    )

    assert result.status == status
    assert result.candidate is not None
    assert result.candidate.risk_tier == tier
    assert result.candidate.execution_authority == authority


def test_incomplete_effect_declaration_fails_closed_to_protected_r3() -> None:
    result = compile_action(
        _draft(complete=False),
        evidence_ids=frozenset({"span:1"}),
        hypothesis_ids=frozenset({"hypothesis:1"}),
        policy=_policy(),
    )

    assert result.status == ActionCompilationStatus.PROTECTED_ACTION
    assert result.candidate is not None
    assert result.candidate.risk_tier == RiskTier.R3
    assert result.candidate.required_approver_role == "project-owner"


def test_unknown_family_without_approver_is_rejected() -> None:
    result = compile_action(
        _draft(family="UNSEEN_ACTION"),
        evidence_ids=frozenset({"span:1"}),
        hypothesis_ids=frozenset({"hypothesis:1"}),
        policy=_policy(),
    )

    assert result.status == ActionCompilationStatus.REJECTED
    assert result.candidate is None
    assert "no configured approver" in result.reason


def test_references_outside_active_context_are_rejected() -> None:
    result = compile_action(
        _draft(),
        evidence_ids=frozenset(),
        hypothesis_ids=frozenset(),
        policy=_policy(),
    )

    assert result.status == ActionCompilationStatus.REJECTED
    assert result.invalid_source_refs == ("span:1",)
    assert result.invalid_hypothesis_refs == ("hypothesis:1",)


def _plan_draft(frontier: tuple[str, ...]) -> ActionPlanDraft:
    return ActionPlanDraft(
        plan_id="plan:1",
        object_id="object:1",
        alternatives=(
            _draft(),
            _draft(
                family="SANDBOX_REPLAY",
                facts=ActionRiskFacts(runs_untrusted_code=True),
            ),
            _draft(
                family="CONTROLLED_RERUN",
                facts=ActionRiskFacts(physical_action=True),
            ),
        ),
        decision_analysis=DecisionAnalysis(
            decision="choose the next discriminating action",
            criteria=(
                DecisionCriterion(
                    criterion_id="decision-criterion:information",
                    name="information value",
                    mandatory=True,
                    rationale="separate competing hypotheses before protected work",
                ),
            ),
            evaluations=(),
            uncertainty="field access remains unknown",
            sensitivity="ranking changes if the read-only comparison resolves the conflict",
        ),
        proposed_frontier=frontier,
        plan_revision_digest="a" * 64,
    )


def test_plan_compiler_keeps_only_r0_to_r2_on_auto_frontier() -> None:
    result = compile_action_plan(
        _plan_draft(("action:read_only_analysis", "action:sandbox_replay")),
        evidence_ids=frozenset({"span:1"}),
        hypothesis_ids=frozenset({"hypothesis:1"}),
        policy=_policy(),
    )

    assert result.status == ActionPlanCompilationStatus.READY
    assert result.plan is not None
    assert result.plan.frontier == (
        "action:read_only_analysis",
        "action:sandbox_replay",
    )


def test_plan_compiler_holds_when_model_places_r3_on_auto_frontier() -> None:
    result = compile_action_plan(
        _plan_draft(("action:controlled_rerun",)),
        evidence_ids=frozenset({"span:1"}),
        hypothesis_ids=frozenset({"hypothesis:1"}),
        policy=_policy(),
    )

    assert result.status == ActionPlanCompilationStatus.HOLD
    assert result.plan is None
    assert "no safe auto frontier" in result.hold_reasons[0]
    assert "removed protected" in result.adjustments[0]


def test_plan_compiler_filters_r3_when_safe_frontier_remains() -> None:
    result = compile_action_plan(
        _plan_draft(("action:read_only_analysis", "action:controlled_rerun")),
        evidence_ids=frozenset({"span:1"}),
        hypothesis_ids=frozenset({"hypothesis:1"}),
        policy=_policy(),
    )

    assert result.status == ActionPlanCompilationStatus.READY
    assert result.plan is not None
    assert result.plan.frontier == ("action:read_only_analysis",)
    assert "removed protected" in result.adjustments[0]
