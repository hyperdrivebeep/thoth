"""Explicit simulated host ownership for isolated Hook tests, never live-host evidence."""

import hashlib
import json
from pathlib import Path
from typing import Any

from scripts.architecture_gate_contract import (
    archive_receipt,
    calculate_preflight_receipt_id,
    preflight_archive_payload,
)


def bind_owner(directory: Path, *, session: str = "session-a") -> dict[str, Any]:
    path = directory / "preflight.json"
    preflight = json.loads(path.read_text(encoding="utf-8"))
    payload: dict[str, Any] = {
        "schema_version": "1.0.0",
        "kind": "THOTH_HOST_PREFLIGHT_REQUEST",
        "session_id": session,
        "turn_id": "turn-fixture",
        "tool_use_id": "call-fixture",
        "request_digest": "a" * 64,
        "observed_at": "2026-09-09T00:00:00+00:00",
        "previous_preflight_receipt_id": None,
        "nonce": "fixture-owner",
    }
    identity = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    owner = {**payload, "owner_receipt_id": identity}
    archive_receipt(directory, receipt_id=identity, kind="host-session", payload=owner)
    preflight["owner_context"] = owner
    preflight["preflight_receipt_id"] = calculate_preflight_receipt_id(preflight)
    archive_receipt(
        directory,
        receipt_id=preflight["preflight_receipt_id"],
        kind="preflight",
        payload=preflight_archive_payload(preflight),
    )
    path.write_text(json.dumps(preflight), encoding="utf-8")
    return preflight
