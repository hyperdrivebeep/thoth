from __future__ import annotations

from thoth.domain.action import ActionCandidate, ActionRiskFacts
from thoth.domain.base import DomainModel
from thoth.domain.enums import ActionState, ExecutionAuthority, RiskTier
from thoth.domain.errors import InvariantViolation


class AuthorityRoute(DomainModel):
    risk_tier: RiskTier
    execution_authority: ExecutionAuthority
    state: ActionState
    reason: str


def classify_authority(facts: ActionRiskFacts) -> AuthorityRoute:
    if any(
        (
            facts.changes_official_kpi,
            facts.grants_waiver,
            facts.changes_safety_threshold,
            facts.finalizes_model_weights,
        )
    ):
        return AuthorityRoute(
            risk_tier=RiskTier.R4,
            execution_authority=ExecutionAuthority.PROHIBITED_R4,
            state=ActionState.PROHIBITED,
            reason="official semantic or safety authority cannot be delegated",
        )
    if any((facts.external_write, facts.physical_action, facts.changes_official_baseline)):
        return AuthorityRoute(
            risk_tier=RiskTier.R3,
            execution_authority=ExecutionAuthority.HUMAN_REQUIRED_R3,
            state=ActionState.APPROVAL_PENDING,
            reason="reality-changing action requires a named human approver",
        )
    if facts.runs_untrusted_code:
        return AuthorityRoute(
            risk_tier=RiskTier.R2,
            execution_authority=ExecutionAuthority.SANDBOX_ONLY_R2,
            state=ActionState.RISK_CLASSIFIED,
            reason="candidate execution is limited to an isolated sandbox",
        )
    if facts.changes_local_draft:
        return AuthorityRoute(
            risk_tier=RiskTier.R1,
            execution_authority=ExecutionAuthority.PREAUTHORIZED_R1,
            state=ActionState.RISK_CLASSIFIED,
            reason="reversible local draft change may be preauthorized",
        )
    return AuthorityRoute(
        risk_tier=RiskTier.R0,
        execution_authority=ExecutionAuthority.AUTO_R0,
        state=ActionState.AUTO_ALLOWED,
        reason="read-only analysis is automatically allowed within project scope",
    )


def validate_action_route(action: ActionCandidate, facts: ActionRiskFacts) -> None:
    route = classify_authority(facts)
    if (
        action.risk_tier != route.risk_tier
        or action.execution_authority != route.execution_authority
    ):
        raise InvariantViolation("action authority does not match deterministic risk facts")
    if action.risk_tier == RiskTier.R4 and action.state != ActionState.PROHIBITED:
        raise InvariantViolation("R4 action must remain prohibited")
