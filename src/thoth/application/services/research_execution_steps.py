"""Bind selected research inputs into copied execution steps without I/O."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thoth.application.services.action_projection import action_view
from thoth.domain.action import ActionCandidate
from thoth.domain.research_identity import ResearchIdentityError
from thoth.domain.sandbox import SandboxRunSpec

if TYPE_CHECKING:
    from thoth.application.services.research_identity_contracts import ResearchContext


def bound_execution_steps(
    context: ResearchContext,
    action: ActionCandidate,
    spec: SandboxRunSpec,
    prediction_refs: tuple[str, ...],
) -> tuple[dict[str, object], ...]:
    current = context.action_plan
    assert current is not None
    by_id = {item.action_id: item for item in context.actions}
    selected = by_id.get(action.action_id)
    if (
        selected is None
        or action_view(selected) != action
        or selected.risk_tier != "R2"
        or not action.effect_completeness_confirmed
    ):
        raise ResearchIdentityError("TEST_EXECUTION_ACTION_BASIS_MISMATCH")
    steps: list[dict[str, object]] = []
    found = False
    for prior in current.steps:
        identifier = prior.get("action_id")
        record = by_id.get(identifier) if isinstance(identifier, str) else None
        if record is None:
            raise ResearchIdentityError("TEST_EXECUTION_STEP_IDENTITY_UNRESOLVED")
        step = {
            **prior,
            "action_revision_digest": record.revision_digest,
            "risk_tier": record.risk_tier,
            "effect_vector": record.effect_vector,
            "required_roles": record.required_roles,
            "required_processes": record.required_processes,
            "policy_state": record.policy_state,
        }
        if identifier == action.action_id:
            if found or prior.get("state", "READY") != "READY":
                raise ResearchIdentityError("TEST_EXECUTION_STEP_NOT_READY")
            found = True
            step.update(
                {
                    "input_digests": tuple(item.content_sha256 for item in spec.input_snapshots),
                    "prediction_refs": prediction_refs,
                    "hypothesis_refs": action.hypothesis_ids,
                    "runtime_binding": {
                        "runtime_profile": spec.runtime_profile.value,
                        "image_digest": spec.image_digest,
                        "argv": spec.argv,
                        "policy_digest": spec.policy_digest,
                    },
                    "policy_state": "PREAUTHORIZED",
                    "state": "READY",
                }
            )
        steps.append(step)
    if not found:
        raise ResearchIdentityError("TEST_EXECUTION_STEP_NOT_IN_PLAN")
    return tuple(steps)
