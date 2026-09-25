"""Historical member bindings must be proved by the target's commit or explicit refs."""

from typing import cast

from thoth.domain.base import DomainModel
from thoth.domain.restore import RestoreError
from thoth.domain.revision import SemanticRevision
from thoth.ports.ledger import LedgerPort


def require_member_basis(
    ledger: LedgerPort,
    target: SemanticRevision,
    record: DomainModel,
    references: tuple[str, ...],
    expected_heads: dict[str, str],
) -> None:
    payload = record.model_dump(mode="python")
    if not references:
        return
    peer_ids = {
        identifier
        for receipt in ledger.read_receipts(target.project_id)
        if target.revision_id in receipt.subject_refs
        for identifier in receipt.subject_refs
    }
    explicit: dict[str, str] = {}
    steps = payload.get("steps", ())
    if isinstance(steps, (tuple, list)):
        for step in cast(tuple[object, ...] | list[object], steps):
            if not isinstance(step, dict):
                raise RestoreError("RESTORE_MEMBERS_REQUIRE_REVIEW")
            values = cast(dict[str, object], step)
            if isinstance(values.get("action_revision_digest"), str) and isinstance(
                values.get("action_id"), str
            ):
                explicit[f"ACTION:{values['action_id']}"] = str(values["action_revision_digest"])
    for key in references:
        digest = expected_heads[key]
        member = ledger.read_revision_by_digest(target.project_id, digest)
        if member is None or (explicit.get(key) != digest and member.revision_id not in peer_ids):
            raise RestoreError("RESTORE_MEMBERS_REQUIRE_REVIEW")


def require_plan_graph(record: DomainModel) -> None:
    from thoth.application.services.action_plan_validation import validate_action_plan_dag

    payload = record.model_dump(mode="python")
    if "steps" not in payload:
        return
    try:
        validate_action_plan_dag(
            cast(tuple[dict[str, object], ...], payload["steps"]),
            cast(tuple[dict[str, str], ...], payload.get("dependency_edges", ())),
        )
    except (ValueError, KeyError) as exc:
        raise RestoreError("RESTORE_PLAN_INVALID") from exc
