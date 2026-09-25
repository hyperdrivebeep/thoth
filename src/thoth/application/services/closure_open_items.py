"""Validate routine followup details without granting risk acceptance authority."""

from thoth.domain.closure import ClosureOpenItem
from thoth.ports.control_record import ControlRecordStorePort
from thoth.ports.ledger import LedgerPort


def open_item_issues(
    project: str,
    items: tuple[ClosureOpenItem, ...],
    ledger: LedgerPort,
    records: ControlRecordStorePort,
) -> dict[str, tuple[str, ...]]:
    heads = ledger.read_heads(project)
    allowed = {"DEFERRED", "ABSTAINED", "CANCELLED", "SUPERSEDED", "ARCHIVED_WITH_OPEN_ITEMS"}
    issues: dict[str, tuple[str, ...]] = {}
    seen: set[str] = set()
    for index, item in enumerate(items):
        missing: list[str] = []
        if not item.item_id or not item.item_id.strip():
            missing.append("ITEM_ID_REQUIRED")
        elif item.item_id in seen:
            missing.append("DUPLICATE_ITEM_ID")
        seen.add(item.item_id or "")
        if item.disposition not in allowed:
            missing.append("DISPOSITION_REQUIRED_OR_UNAUTHORIZED")
        followup = item.followup_ref
        valid_followup = False
        if followup:
            stored = records.read(project, "CLOSURE", followup)
            valid_followup = bool(
                (stored and stored.record_type == "FOLLOWUP" and stored.state == "OPEN")
                or any(f"{kind}:{followup}" in heads for kind in ("ACTION", "DECISION_OBJECT"))
            )
            if not valid_followup:
                missing.append("FOLLOWUP_REF_INVALID")
        if not (item.owner_ref and item.owner_ref.strip()) and not valid_followup:
            missing.append("OWNER_OR_FOLLOWUP_REQUIRED")
        if not item.trigger_or_due or not item.trigger_or_due.strip():
            missing.append("TRIGGER_REQUIRED")
        risk = item.residual_risk
        if risk is None or not risk.description.strip():
            missing.append("RESIDUAL_RISK_REQUIRED")
        elif risk.state == "KNOWN" and not risk.basis_refs:
            missing.append("RESIDUAL_RISK_BASIS_REQUIRED")
        elif any(ledger.read_revision_by_digest(project, ref) is None for ref in risk.basis_refs):
            missing.append("RESIDUAL_RISK_BASIS_INVALID")
        if missing:
            issues[f"{index}:{item.item_id or 'legacy'}"] = tuple(missing)
    return issues
