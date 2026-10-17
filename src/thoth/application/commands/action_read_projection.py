"""Pure Action response projections; storage and mutation orchestration stay in handlers."""

from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from thoth.application.commands.action_inputs import ActionListInput, PortfolioListInput
from thoth.application.services.revision_service import CommitResult
from thoth.domain.action_full import ActionPlanRecord, ActionPortfolioRecord, ActionRecord
from thoth.domain.base import DomainModel
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


def plan_step(plan: ActionPlanRecord, step_id: str) -> dict[str, object]:
    for step in plan.steps:
        if step.get("step_id") == step_id:
            return step
    raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "step not found")


def action_summary(item: ActionRecord) -> dict[str, JsonValue]:
    return {
        "action_id": item.action_id,
        "object_id": item.object_id,
        "portfolio_id": item.portfolio_id,
        "primary_purpose": item.primary_purpose,
        "proposal_state": item.proposal_state,
        "decision_state": item.decision_state,
        "policy_state": item.policy_state,
        "authorization_state": item.authorization_state,
        "risk_tier": item.risk_tier,
        "freshness": item.freshness,
        "revision_digest": item.revision_digest,
    }


def action_revision_result(
    before: ActionRecord, after: ActionRecord, commit: CommitResult
) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        {
            "action": after.model_dump(mode="json"),
            "semantic_diff": semantic_diff(before, after),
            "invalidated_decisions_plans_authorizations_executions": True,
            "commit": commit.model_dump(mode="json"),
        },
    )


def plan_revision_result(
    before: ActionPlanRecord, after: ActionPlanRecord, commit: CommitResult
) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        {
            "plan": after.model_dump(mode="json"),
            "semantic_diff": semantic_diff(before, after),
            "frontier_impact_auth_invalidations": True,
            "commit": commit.model_dump(mode="json"),
        },
    )


def semantic_diff(before: DomainModel, after: DomainModel) -> list[dict[str, JsonValue]]:
    left = before.model_dump(mode="json")
    right = after.model_dump(mode="json")
    return [
        {
            "path": key,
            "before": cast(JsonValue, left.get(key)),
            "after": cast(JsonValue, right.get(key)),
        }
        for key in sorted(set(left) | set(right))
        if left.get(key) != right.get(key)
    ]


def plan_graph_view(plan: ActionPlanRecord) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        {
            "nodes": list(plan.steps),
            "dependency_edges": list(plan.dependency_edges),
            "gates": [
                {
                    "step_id": step["step_id"],
                    "policy_state": step.get("policy_state"),
                    "risk_tier": step.get("risk_tier"),
                }
                for step in plan.steps
            ],
            "parallel_groups": [list(plan.auto_executable_frontier)],
            "point_of_no_return_steps": list(plan.point_of_no_return_steps),
            "current_frontier": list(plan.auto_executable_frontier),
        },
    )


def action_impact_view(action: ActionRecord) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        {
            "direct_impact": action.impact_set,
            "risk_tier": action.risk_tier,
            "required_processes": list(action.required_processes),
            "required_roles": list(action.required_roles),
        },
    )


def plan_impact_view(plan: ActionPlanRecord) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        {
            "cumulative_impact": plan.cumulative_impact,
            "required_process_union": list(plan.required_process_union),
            "required_role_union": list(plan.required_role_union),
        },
    )


def step_impact_view(plan: ActionPlanRecord, step_id: str) -> dict[str, JsonValue]:
    return {"step_impact": cast(JsonValue, plan_step(plan, step_id).get("impact", {}))}


def action_matches(item: ActionRecord, request: ActionListInput) -> bool:
    return (
        (request.object_id is None or item.object_id == request.object_id)
        and (request.portfolio_id is None or item.portfolio_id == request.portfolio_id)
        and (request.purpose is None or item.primary_purpose == request.purpose)
        and (request.proposal_state is None or item.proposal_state == request.proposal_state)
        and (request.policy_state is None or item.policy_state == request.policy_state)
        and (
            request.authorization_state is None
            or item.authorization_state == request.authorization_state
        )
    )


def portfolio_matches(item: ActionPortfolioRecord, request: PortfolioListInput) -> bool:
    return (request.object_id is None or item.object_id == request.object_id) and (
        request.decision_state is None or item.decision_state == request.decision_state
    )
