"""Prepare a protected-action approval bound to the exact content that would be sent."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import cast

from thoth.domain.action_full import (
    ActionAuditRecord,
    ActionPlanRecord,
    AuthorizationEnvelopeRecord,
)
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.protected_action import payload_digest, payload_from_step
from thoth.ports.action import ActionStorePort
from thoth.ports.runtime import AtomicUnitOfWorkPort, ClockPort, IdGeneratorPort


def prepare_authorization(
    plan: ActionPlanRecord,
    step: dict[str, object],
    *,
    predecessor_output_digests: tuple[str, ...],
    target_baseline_digests: tuple[str, ...],
    policy_version: str,
    ids: IdGeneratorPort,
    clock: ClockPort,
    store: ActionStorePort,
    uow: AtomicUnitOfWorkPort,
    audit: Callable[[str, str, str, dict[str, object]], ActionAuditRecord],
) -> tuple[AuthorizationEnvelopeRecord | None, str]:
    """Scope 2.0.0 binds the payload digest, not the whole plan revision, so display-only edits
    do not void an approval. Envelopes already stored as 1.0.0 are read and consumed as before."""

    step_id = str(step["step_id"])
    if step.get("risk_tier") == "R4":
        raise ValueError("R4 has no authorization transition or executor")
    if step.get("risk_tier") != "R3":
        return None, "NOT_REQUIRED"
    predecessors = {edge["from"] for edge in plan.dependency_edges if edge["to"] == step_id}
    if predecessors and len(predecessor_output_digests) < len(predecessors):
        return None, "PRECONDITION_NOT_READY"
    if not target_baseline_digests:
        return None, "PRECONDITION_NOT_READY"
    payload = payload_from_step(step)
    content_digest = payload_digest(payload)
    scope = {
        "project_id": plan.project_id,
        "plan_id": plan.plan_id,
        "step_id": step_id,
        "payload_digest": content_digest,
        "predecessor_output_digests": predecessor_output_digests,
        "target_baseline_digests": target_baseline_digests,
        "policy_version": policy_version,
    }
    digest = domain_digest("ACTION_AUTHORIZATION_SCOPE", "2.0.0", canonical_payload(scope))
    roles = step.get("required_roles", ())
    required_roles = (
        tuple(str(item) for item in cast(tuple[object, ...] | list[object], roles))
        if isinstance(roles, tuple | list)
        else ()
    )
    draft: dict[str, object] = {
        "authorization_revision_id": ids.new("authorization-revision"),
        "authorization_id": ids.new("authorization"),
        "project_id": plan.project_id,
        "plan_id": plan.plan_id,
        "step_id": step_id,
        "plan_revision_digest": plan.revision_digest,
        "predecessor_output_digests": predecessor_output_digests,
        "target_baseline_digests": target_baseline_digests,
        "policy_version": policy_version,
        "exact_scope_digest": digest,
        "required_roles": required_roles,
        "state": "PENDING",
        "decision_history": (),
        "expires_at": clock.now() + timedelta(hours=1),
        "single_use": True,
        "payload": payload.model_dump(mode="json"),
        "payload_digest": content_digest,
        "created_at": clock.now(),
        "schema_version": "2.0.0",
    }
    envelope = AuthorizationEnvelopeRecord.model_validate(
        {
            **draft,
            "revision_digest": domain_digest("AUTHORIZATION", "1.0.0", canonical_payload(draft)),
        }
    )
    with uow.transaction():
        store.add_authorization(envelope)
        audit(
            envelope.project_id,
            envelope.authorization_id,
            "action/authorizationPrepared",
            {"exact_scope_digest": digest},
        )
    return envelope, "PENDING"
