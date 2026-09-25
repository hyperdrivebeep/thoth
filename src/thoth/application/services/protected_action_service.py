from __future__ import annotations

from datetime import datetime

from thoth.domain.action import ActionCandidate, ProtectedActionCard
from thoth.domain.enums import RiskTier
from thoth.domain.errors import InvariantViolation


def prepare_protected_action(
    action: ActionCandidate,
    *,
    plan_revision_digest: str,
    step_id: str,
    exact_input_digests: tuple[str, ...],
    target_revision: str,
    baseline_revision: str | None,
    tool: str,
    environment: str,
    egress: str,
    budget: str,
    time_limit_seconds: int,
    stop_conditions: tuple[str, ...],
    compensation: str,
    required_roles: tuple[str, ...],
    expires_at: datetime,
) -> ProtectedActionCard:
    if action.risk_tier != RiskTier.R3:
        raise InvariantViolation("protected action cards are only for R3 candidates")
    if not required_roles or action.required_approver_role not in required_roles:
        raise InvariantViolation("protected action card must include the required approver role")
    return ProtectedActionCard(
        action_id=action.action_id,
        plan_revision_digest=plan_revision_digest,
        step_id=step_id,
        exact_input_digests=exact_input_digests,
        target_revision=target_revision,
        baseline_revision=baseline_revision,
        tool=tool,
        environment=environment,
        egress=egress,
        budget=budget,
        time_limit_seconds=time_limit_seconds,
        stop_conditions=stop_conditions,
        compensation=compensation,
        required_roles=required_roles,
        expires_at=expires_at,
    )
