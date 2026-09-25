"""Record and consume exact, current non-fungible authorization decisions atomically."""

from __future__ import annotations

from collections.abc import Callable

from thoth.domain.action_full import ActionAuditRecord, AuthorizationEnvelopeRecord
from thoth.domain.auth import require_authenticated_authority
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.ports.action import ActionStorePort
from thoth.ports.governance import GovernanceStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.runtime import ClockPort, IdGeneratorPort


def consume_authorization(
    expected: AuthorizationEnvelopeRecord,
    *,
    exact_scope_digest: str,
    store: ActionStorePort,
    governance: GovernanceStorePort,
    ledger: LedgerPort,
    clock: ClockPort,
    ids: IdGeneratorPort,
    audit: Callable[[str, str, str, dict[str, object]], ActionAuditRecord],
) -> AuthorizationEnvelopeRecord:
    with ledger.transaction():
        current = store.read_authorization(expected.project_id, expected.authorization_id)
        if current is None or current.revision_digest != expected.revision_digest:
            raise ValueError("authorization revision changed before consumption")
        if current.state != "APPROVED":
            raise ValueError("authorization is not approved")
        if current.exact_scope_digest != exact_scope_digest:
            raise ValueError("authorization scope digest changed before consumption")
        now = clock.now()
        if now >= current.expires_at:
            raise ValueError("authorization expired before consumption")
        if current.single_use and current.consumed_at is not None:
            raise ValueError("single-use authorization was already consumed")
        if not current.required_roles or approved_roles(current, governance) != frozenset(
            current.required_roles
        ):
            raise ValueError("authorization approval authority required-role set is incomplete")
        history: dict[str, object] = {"decision": "CONSUMED", "consumed_at": now.isoformat()}
        draft = current.model_dump(mode="python")
        draft.update(
            {
                "authorization_revision_id": ids.new("authorization-revision"),
                "state": "CONSUMED",
                "decision_history": (*current.decision_history, history),
                "consumed_at": now,
                "supersedes_revision_digest": current.revision_digest,
                "created_at": now,
            }
        )
        draft.pop("revision_digest", None)
        revised = AuthorizationEnvelopeRecord.model_validate(
            {
                **draft,
                "revision_digest": domain_digest(
                    "AUTHORIZATION", "1.0.0", canonical_payload(draft)
                ),
            }
        )
        store.add_authorization(revised)
        audit(current.project_id, current.authorization_id, "action/authorizationConsumed", history)
        return revised


def revise_authorization(
    expected: AuthorizationEnvelopeRecord,
    *,
    state: str,
    history: dict[str, object],
    store: ActionStorePort,
    ledger: LedgerPort,
    clock: ClockPort,
    ids: IdGeneratorPort,
    audit: Callable[[str, str, str, dict[str, object]], ActionAuditRecord],
) -> AuthorizationEnvelopeRecord:
    with ledger.transaction():
        current = store.read_authorization(expected.project_id, expected.authorization_id)
        if current is None or current.revision_digest != expected.revision_digest:
            raise ValueError("authorization revision changed before decision")
        if current.state != "PENDING" or current.consumed_at is not None:
            raise ValueError("authorization is not pending")
        draft = current.model_dump(mode="python")
        draft.update(
            {
                "authorization_revision_id": ids.new("authorization-revision"),
                "state": state,
                "decision_history": (*current.decision_history, history),
                "supersedes_revision_digest": current.revision_digest,
                "created_at": clock.now(),
            }
        )
        draft.pop("revision_digest", None)
        revised = AuthorizationEnvelopeRecord.model_validate(
            {
                **draft,
                "revision_digest": domain_digest(
                    "AUTHORIZATION", "1.0.0", canonical_payload(draft)
                ),
            }
        )
        store.add_authorization(revised)
        audit(current.project_id, current.authorization_id, "action/authorizationDecided", history)
        return revised


def approved_roles(
    current: AuthorizationEnvelopeRecord,
    governance: GovernanceStorePort,
    additional_history: tuple[dict[str, object], ...] = (),
) -> frozenset[str]:
    latest: dict[str, dict[str, object]] = {}
    for item in (*current.decision_history, *additional_history):
        role_ref = item.get("role_assignment_ref")
        if isinstance(role_ref, str):
            latest[role_ref] = item
    roles: set[str] = set()
    for role in governance.list_roles(current.project_id):
        vote = latest.get(role.role_assignment_id)
        if (
            vote is not None
            and vote.get("decision") == "APPROVE"
            and vote.get("approved_digest") == current.exact_scope_digest
            and vote.get("actor_ref") == role.actor_id
            and not vote.get("dissent")
            and role.state == "ACTIVE"
            and role.role in current.required_roles
        ):
            roles.add(role.role)
    return frozenset(roles)


def decide_authorization(
    expected: AuthorizationEnvelopeRecord,
    *,
    decision: str,
    actor_ref: str,
    role_assignment_ref: str,
    approved_digest: str,
    reason: str | None,
    dissent: str | None,
    store: ActionStorePort,
    governance: GovernanceStorePort,
    ledger: LedgerPort,
    clock: ClockPort,
    ids: IdGeneratorPort,
    audit: Callable[[str, str, str, dict[str, object]], ActionAuditRecord],
) -> AuthorizationEnvelopeRecord:
    with ledger.transaction():
        require_authenticated_authority(expected.project_id, actor_ref, role_assignment_ref)
        current = store.read_authorization(expected.project_id, expected.authorization_id)
        if current is None or current.revision_digest != expected.revision_digest:
            raise ValueError("authorization revision changed before decision")
        if current.state != "PENDING":
            raise ValueError("authorization is not pending")
        if approved_digest != current.exact_scope_digest:
            raise ValueError("approved digest does not match the content-bound scope")
        role = next(
            (
                item
                for item in governance.list_roles(current.project_id)
                if item.role_assignment_id == role_assignment_ref and item.actor_id == actor_ref
            ),
            None,
        )
        if role is None or role.state != "ACTIVE" or role.role not in current.required_roles:
            raise ValueError("actor lacks a required non-fungible role assignment")
        if decision not in {"APPROVE", "REJECT"}:
            raise ValueError("authorization decision must be APPROVE or REJECT")
        now = clock.now()
        history: dict[str, object] = {
            "decision": decision,
            "actor_ref": actor_ref,
            "role_assignment_ref": role_assignment_ref,
            "approved_digest": approved_digest,
            "reason": reason,
            "dissent": dissent,
            "decided_at": now.isoformat(),
        }
        state = "REJECTED" if decision == "REJECT" else "PENDING"
        if decision == "APPROVE" and approved_roles(current, governance, (history,)) == frozenset(
            current.required_roles
        ):
            state = "APPROVED"
        if now >= current.expires_at:
            state = "EXPIRED"
            history["decision"] = "EXPIRED"
        return revise_authorization(
            current,
            state=state,
            history=history,
            store=store,
            ledger=ledger,
            clock=clock,
            ids=ids,
            audit=audit,
        )
