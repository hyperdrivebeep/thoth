"""Whether an approval still covers its step, and marking it stale when it no longer does."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime

from pydantic import JsonValue

from thoth.domain.action_full import (
    ActionAuditRecord,
    ActionPlanRecord,
    AuthorizationEnvelopeRecord,
)
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.protected_action import (
    MaterialChange,
    ProtectedActionPayload,
    material_changes,
    payload_digest,
    payload_from_step,
)
from thoth.ports.action import ActionStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.runtime import ClockPort, IdGeneratorPort

CURRENT_SCHEMA = "2.0.0"


def authorization_applies(
    item: AuthorizationEnvelopeRecord, plan: ActionPlanRecord, step: Mapping[str, object]
) -> bool:
    """Is the envelope bound to what this step would do now?

    2.0.0 envelopes compare the sent payload. A 1.0.0 envelope was bound to a whole plan revision
    and keeps that rule; it is never silently rewritten to 2.0.0.
    """

    if item.schema_version == CURRENT_SCHEMA:
        return item.payload_digest == payload_digest(payload_from_step(step))
    return item.plan_revision_digest == plan.revision_digest


def authorization_read_view(
    item: AuthorizationEnvelopeRecord,
    plan: ActionPlanRecord | None,
    *,
    now: datetime,
    currentness: Callable[[str, str, str], dict[str, JsonValue]],
) -> dict[str, JsonValue]:
    """The record plus whether its content still matches the plan and whether a look is advised.

    review_needed is only a notice: the payload is unchanged, so the approval is not voided.
    """

    step = (
        None
        if plan is None
        else next((s for s in plan.steps if s.get("step_id") == item.step_id), None)
    )
    covered = plan is not None and step is not None and authorization_applies(item, plan, step)
    plan_state = ""
    if plan is not None:
        plan_state = str(
            currentness(item.project_id, plan.plan_id, plan.revision_digest).get("state")
        )
    return {
        "authorization": item.model_dump(mode="json"),
        "effective_state": "EXPIRED"
        if item.state == "PENDING" and now >= item.expires_at
        else item.state,
        "payload_current": covered,
        "review_needed": covered and plan_state != "CURRENT",
    }


def approved_authorization(
    envelopes: tuple[AuthorizationEnvelopeRecord, ...],
    plan: ActionPlanRecord,
    step: Mapping[str, object],
) -> AuthorizationEnvelopeRecord | None:
    """The approved envelope that still covers this step, if any."""

    return next(
        (
            item
            for item in envelopes
            if item.step_id == step.get("step_id")
            and item.state == "APPROVED"
            and authorization_applies(item, plan, step)
        ),
        None,
    )


@dataclass(frozen=True)
class StaleDecision:
    envelope: AuthorizationEnvelopeRecord
    changes: tuple[MaterialChange, ...]


def stale_decisions(
    envelopes: tuple[AuthorizationEnvelopeRecord, ...], revised: ActionPlanRecord
) -> tuple[StaleDecision, ...]:
    """Open 2.0.0 approvals of this plan whose step payload no longer matches."""

    result: list[StaleDecision] = []
    for item in envelopes:
        if (
            item.schema_version != CURRENT_SCHEMA
            or item.plan_id != revised.plan_id
            or item.state not in {"APPROVED", "PENDING"}
            or item.payload is None
        ):
            continue
        step = next((s for s in revised.steps if s.get("step_id") == item.step_id), None)
        if step is not None and payload_digest(payload_from_step(step)) == item.payload_digest:
            continue
        if step is None:
            changes = (MaterialChange(field="step", label="단계", before="있음", after="삭제됨"),)
        else:
            changes = material_changes(
                ProtectedActionPayload.model_validate(item.payload), payload_from_step(step)
            )
        result.append(StaleDecision(item, changes))
    return tuple(result)


def mark_stale(
    decision: StaleDecision,
    revised: ActionPlanRecord,
    *,
    store: ActionStorePort,
    ledger: LedgerPort,
    clock: ClockPort,
    ids: IdGeneratorPort,
    audit: Callable[[str, str, str, dict[str, object]], ActionAuditRecord],
) -> AuthorizationEnvelopeRecord:
    """Append a STALE revision; the earlier decisions stay as history of what was approved."""

    with ledger.transaction():
        current = store.read_authorization(
            decision.envelope.project_id, decision.envelope.authorization_id
        )
        if current is None or current.revision_digest != decision.envelope.revision_digest:
            raise ValueError("authorization revision changed before it was marked stale")
        now = clock.now()
        history: dict[str, object] = {
            "decision": "STALE",
            "stale_at": now.isoformat(),
            "plan_revision_digest": revised.revision_digest,
        }
        draft = current.model_dump(mode="python")
        draft.update(
            {
                "authorization_revision_id": ids.new("authorization-revision"),
                "state": "STALE",
                "stale_reason": "MATERIAL_CHANGE",
                "material_changes": tuple(
                    change.model_dump(mode="json") for change in decision.changes
                ),
                "decision_history": (*current.decision_history, history),
                "supersedes_revision_digest": current.revision_digest,
                "created_at": now,
            }
        )
        draft.pop("revision_digest", None)
        stale = AuthorizationEnvelopeRecord.model_validate(
            {
                **draft,
                "revision_digest": domain_digest(
                    "AUTHORIZATION", "1.0.0", canonical_payload(draft)
                ),
            }
        )
        store.add_authorization(stale)
        audit(current.project_id, current.authorization_id, "action/authorizationStale", history)
        return stale
