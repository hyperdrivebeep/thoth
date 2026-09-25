from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.architecture_contract import ROOT
from scripts.architecture_gate_contract import (
    archive_receipt,
    calculate_cancellation_receipt_id,
    preflight_archive_payload,
    validate_archived_receipt,
    validate_preflight_identity,
)

GATE_DIR = Path(os.environ.get("THOTH_GATE_DIR", ROOT / ".thoth" / "architecture"))
PREFLIGHT = GATE_DIR / "preflight.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()
    if (GATE_DIR / "pending-rule-restore.json").exists():
        raise ValueError("finish pending rule restoration before cancelling")
    if (GATE_DIR / "pending-rule-change.json").exists():
        raise ValueError("finish pending metadata transaction before cancelling")
    if not PREFLIGHT.is_file():
        raise FileNotFoundError("no active architecture preflight")
    payload = json.loads(PREFLIGHT.read_text(encoding="utf-8"))
    original = dict(payload)
    validate_preflight_identity(
        payload,
        allowed_statuses=frozenset({"READY_FOR_EDIT"}),
    )
    validate_archived_receipt(
        GATE_DIR,
        receipt_id=str(payload["preflight_receipt_id"]),
        kind="preflight",
        payload=preflight_archive_payload(payload),
    )
    from scripts.hook_owner_contract import owner_context

    owner_context(payload, GATE_DIR)
    payload.update(
        {
            "status": "CANCELLED",
            "cancel_reason": args.reason,
            "cancelled_at": datetime.now(UTC).isoformat(),
        }
    )
    payload["cancellation_receipt_id"] = calculate_cancellation_receipt_id(payload)
    archive_receipt(
        GATE_DIR,
        receipt_id=str(payload["preflight_receipt_id"]),
        kind="preflight",
        payload=preflight_archive_payload(payload),
    )
    archive_receipt(
        GATE_DIR,
        receipt_id=str(payload["cancellation_receipt_id"]),
        kind="cancellation",
        payload=payload,
    )
    temporary = PREFLIGHT.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if json.loads(PREFLIGHT.read_bytes()) != original:
        raise ValueError("another preflight replaced the pointer during cancellation")
    if (GATE_DIR / "pending-rule-change.json").exists():
        raise ValueError("metadata transaction started during cancellation")
    os.replace(temporary, PREFLIGHT)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
