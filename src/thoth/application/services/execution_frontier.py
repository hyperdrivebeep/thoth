"""Compute dispatch eligibility without blocking publication of completed effects."""

from thoth.application.services.action_currentness import require_plan_current
from thoth.domain.action_full import ActionPlanRecord
from thoth.ports.action import ActionStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.runtime import ClockPort


def execution_frontier(
    plan: ActionPlanRecord,
    completed_steps: tuple[str, ...],
    selected_step_ids: tuple[str, ...],
    actions: ActionStorePort,
    ledger: LedgerPort,
    clock: ClockPort,
) -> tuple[tuple[str, ...], dict[str, str], tuple[str, ...], tuple[str, ...]]:
    completed = set(completed_steps)
    selected = set(selected_step_ids)
    if selected and (
        len(selected) != len(selected_step_ids)
        or not selected.issubset(str(step["step_id"]) for step in plan.steps)
    ):
        raise ValueError("EXECUTION_SELECTION_INVALID")
    try:
        require_plan_current(ledger, actions, plan)
    except ValueError as exc:
        if str(exc) != "DEPENDENCY_REVIEW_REQUIRED":
            raise
        # Completed effects still finalize; only remaining dispatch is held.
        pending = {
            str(step["step_id"]): "DEPENDENCY_REVIEW_REQUIRED"
            for step in plan.steps
            if str(step["step_id"]) not in completed
            and (not selected or str(step["step_id"]) in selected)
        }
        return (), pending, (), ()
    executable: list[str] = []
    blocked: dict[str, str] = {}
    protected: list[str] = []
    prohibited: list[str] = []
    auth = actions.list_authorizations(plan.project_id, plan.plan_id)
    for step in plan.steps:
        step_id = str(step["step_id"])
        if selected and step_id not in selected:
            continue
        if step_id in completed:
            continue
        predecessors = {edge["from"] for edge in plan.dependency_edges if edge["to"] == step_id}
        if not predecessors.issubset(completed):
            blocked[step_id] = "DEPENDENCY_NOT_SATISFIED"
            continue
        risk = step.get("risk_tier")
        if risk == "R4":
            prohibited.append(step_id)
            continue
        if risk == "R3":
            envelope = next(
                (
                    item
                    for item in auth
                    if item.step_id == step_id
                    and item.plan_revision_digest == plan.revision_digest
                    and item.state == "APPROVED"
                    and item.consumed_at is None
                    and clock.now() < item.expires_at
                ),
                None,
            )
            if envelope is None:
                protected.append(step_id)
                continue
        executable.append(step_id)
    return tuple(executable), blocked, tuple(protected), tuple(prohibited)
