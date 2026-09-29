"""Self-contained preflight input for portable protocol tests, never product proof."""

from __future__ import annotations

from pathlib import Path

from scripts.architecture_contract import load_manifest, rule_bundle_digest
from scripts.architecture_gate_contract import (
    archive_receipt,
    calculate_preflight_receipt_id,
    preflight_archive_payload,
    validate_preflight,
)
from scripts.required_architecture_checks import CHECKS


def synthetic_preflight(
    root: Path,
    *,
    scopes: tuple[str, ...],
    owner_context: dict[str, object] | None = None,
) -> dict[str, object]:
    """Build a valid, explicitly simulated receipt in the fixture's own gate directory."""
    draft: dict[str, object] = {
        "schema_version": "1.0.0",
        "acceptance_id": "A11",
        "mode": "IMPLEMENTATION",
        "declared_scope": list(scopes),
        "planned_remediations": [],
        "rule_bundle_digest": rule_bundle_digest(load_manifest()),
        "baseline_checks": [
            {"check": name, "verdict": "PASS", "errors": [], "exit_code": 0} for name in CHECKS
        ],
        "note": "SYNTHETIC_PROTOCOL_FIXTURE; not a repository verification result",
        "status": "READY_FOR_EDIT",
        "created_at": "2026-09-27T00:00:00+00:00",
    }
    if owner_context is None:
        draft.update({"plan_id": "FIXTURE_ONLY", "node_id": "FIXTURE_NODE"})
    else:
        draft["owner_context"] = owner_context
    receipt_id = calculate_preflight_receipt_id(draft)
    value = {**draft, "preflight_receipt_id": receipt_id}
    validate_preflight(value, allowed_statuses=frozenset({"READY_FOR_EDIT"}))
    gate = root / ".thoth" / "architecture"
    if owner_context is not None:
        owner_id = owner_context.get("owner_receipt_id")
        if not isinstance(owner_id, str):
            raise ValueError("synthetic owner receipt identity is missing")
        archive_receipt(gate, receipt_id=owner_id, kind="host-session", payload=owner_context)
    archive_receipt(
        gate,
        receipt_id=receipt_id,
        kind="preflight",
        payload=preflight_archive_payload(value),
    )
    return value
