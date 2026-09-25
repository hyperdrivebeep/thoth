"""Restore only archived, preflight-bound rule bytes; never grant fresh edit rights."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

from scripts.architecture_gate_contract import (
    archive_receipt,
    preflight_archive_payload,
    validate_archived_receipt,
    validate_preflight_identity,
)


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def capture_snapshot(root: Path, gate: Path, paths: tuple[str, ...], rule_digest: str) -> str:
    files = []
    for relative in paths:
        path = _safe_path(root, relative)
        files.append({"path": relative, "bytes": base64.b64encode(path.read_bytes()).decode()})
    draft = {"rule_bundle_digest": rule_digest, "files": files}
    identity = _digest(draft)
    archive_receipt(gate, receipt_id=identity, kind="rule-snapshot", payload=draft)
    return identity


def _safe_path(root: Path, relative: str) -> Path:
    path = root / relative
    if (
        path.is_symlink() or not path.resolve().is_relative_to(root.resolve())
        or path.resolve().relative_to(root.resolve()).as_posix() != relative
        or relative.startswith((".git/", ".thoth/", ".codex/verification/"))
    ):
        raise ValueError("rule snapshot path is not canonical")
    return path


def load_snapshot(root: Path, gate: Path, preflight: dict[str, Any]) -> dict[str, bytes]:
    validate_preflight_identity(preflight, allowed_statuses=frozenset({"READY_FOR_EDIT"}))
    validate_archived_receipt(
        gate, receipt_id=preflight["preflight_receipt_id"], kind="preflight",
        payload=preflight_archive_payload(preflight),
    )
    identity = preflight.get("rule_snapshot_id", "")
    if not isinstance(identity, str) or len(identity) != 64 or any(
        char not in "0123456789abcdef" for char in identity
    ):
        raise ValueError("preflight has no bound rule snapshot; owner recovery required")
    value = json.loads((gate / "receipts" / f"{identity}.rule-snapshot.json").read_bytes())
    if _digest(value) != identity or value["rule_bundle_digest"] != preflight["rule_bundle_digest"]:
        raise ValueError("rule snapshot digest mismatch")
    result: dict[str, bytes] = {}
    for entry in value["files"]:
        relative = entry["path"]
        _safe_path(root, relative)
        if relative in result:
            raise ValueError("duplicate snapshot path")
        result[relative] = base64.b64decode(entry["bytes"], validate=True)
    return result


def recovery_preview(root: Path, gate: Path, preflight: dict[str, Any]) -> dict[str, Any]:
    snapshot = load_snapshot(root, gate, preflight)
    changes = []
    for relative, before in snapshot.items():
        path = _safe_path(root, relative)
        current = path.read_bytes() if path.is_file() else None
        if current == before:
            continue
        if not any(
            relative == scope or relative.startswith(scope.rstrip("/") + "/")
            for scope in preflight["declared_scope"]
        ):
            raise ValueError("rule drift outside preflight scope; owner recovery required")
        changes.append({
            "path": relative,
            "current_sha256": None if current is None else hashlib.sha256(current).hexdigest(),
            "restore_sha256": hashlib.sha256(before).hexdigest(),
        })
    draft = {"preflight_receipt_id": preflight["preflight_receipt_id"], "changes": changes}
    return {**draft, "drift_id": _digest(draft)}


def restore_rules(
    root: Path, gate: Path, preflight: dict[str, Any], expected_drift: str
) -> dict[str, Any]:
    # A preflight snapshot alone is not an exact, pre-approved change transaction.
    # Keep preview/history compatibility, but never retroactively authorize arbitrary drift.
    raise ValueError(
        "No prepared metadata transaction; snapshot restoration is diagnostic-only. "
        "Use change_architecture_rules.py with an existing pending proposal."
    )

