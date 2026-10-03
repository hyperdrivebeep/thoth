"""Read envelopes preserve historical Action bytes while exposing execution eligibility."""

from pydantic import JsonValue

from thoth.application.services.action_service import ActionService
from thoth.application.services.research_freshness import ResearchFreshnessService
from thoth.application.services.running_research import research_is_running
from thoth.domain.action_full import ActionPlanRecord, ActionRecord, AuthorizationEnvelopeRecord
from thoth.ports.action import ActionStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.operation import OperationStorePort


def action_read_view(
    action: ActionRecord, service: ActionService, operations: OperationStorePort | None = None
) -> dict[str, JsonValue]:
    return {
        "action": action.model_dump(mode="json"),
        "currentness": service.currentness(
            action.project_id, action.action_id, action.revision_digest
        ),
        # A person's estimate is refused while an investigation runs, so the screen can say so.
        "research_running": operations is not None
        and research_is_running(operations, action.project_id),
    }


def plan_read_view(
    plan: ActionPlanRecord,
    authorizations: tuple[AuthorizationEnvelopeRecord, ...],
    service: ActionService,
) -> dict[str, JsonValue]:
    return {
        "plan": plan.model_dump(mode="json"),
        "currentness": service.currentness(plan.project_id, plan.plan_id, plan.revision_digest),
        "authorizations": [item.model_dump(mode="json") for item in authorizations],
    }


def require_plan_current(
    ledger: LedgerPort, actions: ActionStorePort, plan: ActionPlanRecord
) -> None:
    freshness = ResearchFreshnessService(ledger)
    freshness.require_action_eligible(
        plan.project_id, f"ACTION:{plan.plan_id}", plan.revision_digest
    )
    for action_id in plan.selected_action_refs:
        action = actions.read_action(plan.project_id, action_id, None)
        if action is None:
            raise ValueError("DEPENDENCY_REVIEW_REQUIRED")
        freshness.require_action_eligible(
            plan.project_id, f"ACTION:{action_id}", action.revision_digest
        )


def find_plan_step(plan: ActionPlanRecord, step_id: str) -> dict[str, object]:
    for step in plan.steps:
        if step.get("step_id") == step_id:
            return step
    raise ValueError("ActionPlan step not found")
