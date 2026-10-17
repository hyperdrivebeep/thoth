"""Action impact reads with explicit storage; authorization and mutation stay in the facade."""

from pydantic import JsonValue

from thoth.application.commands.action_inputs import ImpactReadInput
from thoth.application.commands.action_read_projection import (
    action_impact_view,
    plan_impact_view,
    step_impact_view,
)
from thoth.ports.action import ActionStorePort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


def read_impact(store: ActionStorePort, request: ImpactReadInput) -> dict[str, JsonValue]:
    if request.action_id:
        action = store.read_action(request.project_id, request.action_id, request.revision_digest)
        if action is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "Action not found")
        return action_impact_view(action)
    if request.plan_id:
        plan = store.read_plan(request.project_id, request.plan_id, request.revision_digest)
        if plan is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "plan not found")
        if request.step_id:
            return step_impact_view(plan, request.step_id)
        return plan_impact_view(plan)
    raise RpcApplicationError(
        RpcErrorCode.DOMAIN_REJECTED, "impact query needs action_id or plan_id"
    )
