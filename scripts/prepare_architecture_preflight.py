from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.architecture_contract import (
    ROOT,
    acceptance_ids,
    load_manifest,
    rule_bundle_digest,
    rule_bundle_paths,
)
from scripts.architecture_gate_contract import (
    archive_receipt,
    calculate_preflight_receipt_id,
)
from scripts.required_architecture_checks import CHECKS as CHECKS
from scripts.required_architecture_checks import run_checks
from scripts.rule_recovery_contract import capture_snapshot

GATE_DIR = Path(os.environ.get("THOTH_GATE_DIR", ROOT / ".thoth" / "architecture"))
PREFLIGHT = GATE_DIR / "preflight.json"


def _atomic_write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _normalized_scope(raw: str) -> str:
    candidate = (ROOT / raw).resolve()
    try:
        return candidate.relative_to(ROOT).as_posix()
    except ValueError as exc:
        raise ValueError(f"scope escapes repository: {raw}") from exc


def _run_checks() -> list[dict[str, object]]:
    return run_checks(ROOT)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--acceptance", required=True)
    parser.add_argument("--scope", action="append", required=True)
    parser.add_argument("--remediates", action="append", default=[])
    parser.add_argument("--note", default="")
    parser.add_argument("--plan-id")
    parser.add_argument("--node-id")
    parser.add_argument("--host-owner-receipt")
    args = parser.parse_args()

    if (GATE_DIR / "pending-rule-restore.json").exists():
        raise ValueError("finish pending rule restoration before preparing a new preflight")
    if (GATE_DIR / "pending-rule-change.json").exists():
        raise ValueError("resume or roll back the exact pending metadata transaction first")
    previous_pointer = PREFLIGHT.read_bytes() if PREFLIGHT.exists() else None
    from scripts.hook_owner_contract import consume_owner, load_requested_owner

    owner = load_requested_owner(
        GATE_DIR, args.host_owner_receipt, sys.argv[1:],
        None if previous_pointer is None else json.loads(previous_pointer),
    )
    if previous_pointer is not None:
        from scripts.rule_transaction_contract import require_publication

        require_publication(GATE_DIR, json.loads(previous_pointer))

    manifest = load_manifest()
    if args.acceptance not in acceptance_ids(manifest):
        raise ValueError(f"unknown Acceptance ID: {args.acceptance}")
    known = {str(item["id"]): item for item in manifest["known_exceptions"]}
    planned = set(args.remediates)
    unknown = planned - set(known)
    if unknown:
        raise ValueError(f"unknown ratchet exceptions: {sorted(unknown)}")
    blockers = {
        identifier
        for identifier, item in known.items()
        if args.acceptance in item["blocks_acceptance"]
    }
    missing = blockers - planned
    if missing:
        raise ValueError(
            f"{args.acceptance} remains blocked; declare planned remediation for {sorted(missing)}"
        )
    scopes = tuple(dict.fromkeys(_normalized_scope(item) for item in args.scope))
    before_rules = rule_bundle_digest(manifest)
    checks = _run_checks()
    if rule_bundle_digest(load_manifest()) != before_rules:
        raise ValueError("rule bundle changed during preflight checks")
    snapshot_id = capture_snapshot(ROOT, GATE_DIR, rule_bundle_paths(manifest), before_rules)
    if rule_bundle_digest(load_manifest()) != before_rules:
        raise ValueError("rule bundle changed while capturing snapshot")
    draft: dict[str, object] = {
        "schema_version": "1.0.0",
        "acceptance_id": args.acceptance,
        "mode": "IMPLEMENTATION",
        "declared_scope": scopes,
        "planned_remediations": tuple(sorted(planned)),
        "rule_bundle_digest": before_rules,
        "rule_snapshot_id": snapshot_id,
        "baseline_checks": checks,
        "note": args.note,
        "status": "READY_FOR_EDIT",
        "created_at": datetime.now(UTC).isoformat(),
    }
    if args.plan_id is not None or args.node_id is not None:
        draft.update({"plan_id": args.plan_id, "node_id": args.node_id})
    if owner is not None:
        draft["owner_context"] = owner
    receipt_id = calculate_preflight_receipt_id(draft)
    payload = {**draft, "preflight_receipt_id": receipt_id}
    archive_receipt(
        GATE_DIR,
        receipt_id=receipt_id,
        kind="preflight",
        payload=payload,
    )
    if (PREFLIGHT.read_bytes() if PREFLIGHT.exists() else None) != previous_pointer:
        raise ValueError("another preflight replaced the pointer during preparation")
    if (GATE_DIR / "pending-rule-change.json").exists():
        raise ValueError("metadata transaction started during preparation")
    if owner is not None:
        consume_owner(GATE_DIR, owner, receipt_id)
    _atomic_write(PREFLIGHT, payload)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
