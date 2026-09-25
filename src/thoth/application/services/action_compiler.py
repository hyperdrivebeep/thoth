from __future__ import annotations

from thoth.application.reducers.authority_router import classify_authority
from thoth.domain.action import (
    ActionCandidate,
    ActionCompilationPolicy,
    ActionCompilationResult,
    ActionDraft,
    ActionPlan,
    ActionPlanCompilationResult,
    ActionPlanDraft,
)
from thoth.domain.enums import (
    ActionCompilationStatus,
    ActionPlanCompilationStatus,
    ActionState,
    ExecutionAuthority,
    RiskTier,
)

_TIER_ORDER = {
    RiskTier.R0: 0,
    RiskTier.R1: 1,
    RiskTier.R2: 2,
    RiskTier.R3: 3,
    RiskTier.R4: 4,
}

_AUTHORITY = {
    RiskTier.R0: (ExecutionAuthority.AUTO_R0, ActionState.AUTO_ALLOWED),
    RiskTier.R1: (ExecutionAuthority.PREAUTHORIZED_R1, ActionState.RISK_CLASSIFIED),
    RiskTier.R2: (ExecutionAuthority.SANDBOX_ONLY_R2, ActionState.RISK_CLASSIFIED),
    RiskTier.R3: (ExecutionAuthority.HUMAN_REQUIRED_R3, ActionState.APPROVAL_PENDING),
    RiskTier.R4: (ExecutionAuthority.PROHIBITED_R4, ActionState.PROHIBITED),
}


def compile_action(
    draft: ActionDraft,
    *,
    evidence_ids: frozenset[str],
    hypothesis_ids: frozenset[str],
    policy: ActionCompilationPolicy,
) -> ActionCompilationResult:
    invalid_sources = tuple(sorted(set(draft.source_refs) - evidence_ids))
    invalid_hypotheses = tuple(sorted(set(draft.hypothesis_ids) - hypothesis_ids))
    if invalid_sources or invalid_hypotheses:
        return ActionCompilationResult(
            status=ActionCompilationStatus.REJECTED,
            reason="draft references evidence or hypotheses outside the active context",
            invalid_source_refs=invalid_sources,
            invalid_hypothesis_refs=invalid_hypotheses,
        )
    if not draft.source_refs and not draft.missing_evidence:
        return ActionCompilationResult(
            status=ActionCompilationStatus.REJECTED,
            reason="draft requires source references or an explicit evidence gap",
        )

    facts_route = classify_authority(draft.effect_facts)
    policy_tier = policy.minimum_tier_by_family.get(draft.action_family, policy.unknown_family_tier)
    minimum_tier = (
        policy_tier
        if draft.effect_completeness_confirmed
        else _higher_tier(policy_tier, RiskTier.R3)
    )
    final_tier = _higher_tier(facts_route.risk_tier, minimum_tier)
    authority, state = _AUTHORITY[final_tier]
    approver = None
    if final_tier == RiskTier.R3:
        approver = policy.approver_role_by_family.get(draft.action_family)
        if not approver:
            return ActionCompilationResult(
                status=ActionCompilationStatus.REJECTED,
                reason="R3 action family has no configured approver role",
            )

    candidate = ActionCandidate(
        primary_purpose=draft.primary_purpose,
        effect_facts=draft.effect_facts,
        effect_completeness_confirmed=draft.effect_completeness_confirmed,
        action_id=draft.action_id,
        object_id=draft.object_id,
        hypothesis_ids=draft.hypothesis_ids,
        action_family=draft.action_family,
        specification=draft.specification,
        expected_information_value=draft.expected_information_value,
        estimated_cost=draft.estimated_cost,
        estimated_seconds=draft.estimated_seconds,
        risk_tier=final_tier,
        execution_authority=authority,
        reversibility=draft.reversibility,
        external_write=draft.effect_facts.external_write,
        sandbox_required=final_tier == RiskTier.R2,
        state=state,
        required_approver_role=approver,
        source_refs=draft.source_refs,
        missing_evidence=draft.missing_evidence,
    )
    status = {
        RiskTier.R0: ActionCompilationStatus.AUTO_CANDIDATE,
        RiskTier.R1: ActionCompilationStatus.AUTO_CANDIDATE,
        RiskTier.R2: ActionCompilationStatus.AUTO_CANDIDATE,
        RiskTier.R3: ActionCompilationStatus.PROTECTED_ACTION,
        RiskTier.R4: ActionCompilationStatus.PROHIBITED,
    }[final_tier]
    reasons = [facts_route.reason]
    if not draft.effect_completeness_confirmed:
        reasons.append(
            "effect declaration is incomplete, so authority is fail-closed at R3 or above"
        )
    if _TIER_ORDER[policy_tier] > _TIER_ORDER[facts_route.risk_tier]:
        reasons.append("Project policy requires a higher minimum tier for this action family")
    return ActionCompilationResult(
        status=status,
        candidate=candidate,
        reason="; ".join(reasons),
    )


def compile_action_plan(
    draft: ActionPlanDraft,
    *,
    evidence_ids: frozenset[str],
    hypothesis_ids: frozenset[str],
    policy: ActionCompilationPolicy,
) -> ActionPlanCompilationResult:
    results = tuple(
        compile_action(
            action,
            evidence_ids=evidence_ids,
            hypothesis_ids=hypothesis_ids,
            policy=policy,
        )
        for action in draft.alternatives
    )
    rejected = tuple(result for result in results if result.candidate is None)
    candidates = tuple(result.candidate for result in results if result.candidate is not None)
    reasons: list[str] = [result.reason for result in rejected]
    adjustments: list[str] = []
    candidate_by_id = {candidate.action_id: candidate for candidate in candidates}
    frontier: list[str] = []
    for action_id in draft.proposed_frontier:
        candidate = candidate_by_id.get(action_id)
        if candidate is None:
            reasons.append(f"proposed frontier references rejected or unknown action {action_id}")
            continue
        if candidate.risk_tier in {RiskTier.R3, RiskTier.R4}:
            adjustments.append(
                f"removed protected or prohibited action {action_id} from auto frontier"
            )
            continue
        frontier.append(action_id)
    if not frontier:
        reasons.append("compiled plan has no safe auto frontier")
    if len(candidates) < 2 or len({candidate.action_family for candidate in candidates}) < 2:
        reasons.append("compiled plan requires at least two materially different action families")
    if rejected or reasons:
        return ActionPlanCompilationResult(
            status=ActionPlanCompilationStatus.HOLD,
            action_results=results,
            hold_reasons=tuple(dict.fromkeys(reasons)),
            adjustments=tuple(dict.fromkeys(adjustments)),
        )
    plan = ActionPlan(
        plan_id=draft.plan_id,
        object_id=draft.object_id,
        alternatives=candidates,
        decision_analysis=draft.decision_analysis,
        frontier=tuple(frontier),
        plan_revision_digest=draft.plan_revision_digest,
    )
    return ActionPlanCompilationResult(
        status=ActionPlanCompilationStatus.READY,
        plan=plan,
        action_results=results,
        adjustments=tuple(dict.fromkeys(adjustments)),
    )


def _higher_tier(left: RiskTier, right: RiskTier) -> RiskTier:
    return left if _TIER_ORDER[left] >= _TIER_ORDER[right] else right
