from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.architecture_gate_contract import archive_receipt, calculate_preflight_receipt_id
from scripts.required_architecture_checks import CHECKS
from scripts.rule_recovery_contract import capture_snapshot, recovery_preview, restore_rules


def fixture(tmp_path: Path) -> tuple[Path, Path, dict[str, object]]:
    root, gate = tmp_path / "repo", tmp_path / "gate"
    (root / "config").mkdir(parents=True)
    (root / "config/a.json").write_bytes(b'{"baseline": 1}\n')
    (root / "config/b.json").write_bytes(b'{"baseline": 2}\n')
    snapshot_id = capture_snapshot(
        root, gate, ("config/a.json", "config/b.json"), "a" * 64
    )
    draft: dict[str, object] = {
        "schema_version": "1.0.0", "acceptance_id": "A11", "mode": "IMPLEMENTATION",
        "declared_scope": ["config"], "planned_remediations": [],
        "rule_bundle_digest": "a" * 64, "rule_snapshot_id": snapshot_id,
        "baseline_checks": [
            {"check": name, "verdict": "PASS", "errors": [], "exit_code": 0} for name in CHECKS
        ],
        "note": "UNIT TEST ONLY; not product acceptance", "status": "READY_FOR_EDIT",
        "created_at": "2026-09-08T00:00:00Z",
    }
    identity = calculate_preflight_receipt_id(draft)
    value: dict[str, object] = {**draft, "preflight_receipt_id": identity}
    archive_receipt(gate, receipt_id=identity, kind="preflight", payload=value)
    (gate / "preflight.json").write_text(json.dumps(value), encoding="utf-8")
    return root, gate, value


def test_snapshot_without_transaction_cannot_authorize_restore(tmp_path: Path) -> None:
    root, gate, preflight = fixture(tmp_path)
    target = root / "config/a.json"
    target.write_bytes(b"{broken")
    (root / "keep.txt").write_bytes(b"user dirty work")
    preview = recovery_preview(root, gate, preflight)
    with pytest.raises(ValueError, match="diagnostic-only"):
        restore_rules(root, gate, preflight, preview["drift_id"])
    assert target.read_bytes() == b"{broken"
    assert (root / "keep.txt").read_bytes() == b"user dirty work"
    assert not (gate / "pending-rule-restore.json").exists()


def test_stale_preview_and_forged_archive_are_rejected(tmp_path: Path) -> None:
    root, gate, preflight = fixture(tmp_path)
    target = root / "config/a.json"
    target.write_bytes(b"broken")
    preview = recovery_preview(root, gate, preflight)
    target.write_bytes(b"another person's change")
    with pytest.raises(ValueError, match="diagnostic-only"):
        restore_rules(root, gate, preflight, preview["drift_id"])
    assert target.read_bytes() == b"another person's change"
    preflight["declared_scope"] = ["scripts"]
    with pytest.raises(ValueError, match="identity"):
        recovery_preview(root, gate, preflight)


def test_unknown_pending_transaction_is_not_reconstructed_from_snapshot(tmp_path: Path) -> None:
    root, gate, preflight = fixture(tmp_path)
    (gate / "pending-rule-restore.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="prepared metadata transaction"):
        restore_rules(root, gate, preflight, "a" * 64)


def test_corrupt_snapshot_never_restores(tmp_path: Path) -> None:
    root, gate, preflight = fixture(tmp_path)
    path = gate / "receipts" / f"{preflight['rule_snapshot_id']}.rule-snapshot.json"
    value = json.loads(path.read_bytes())
    value["files"][0]["bytes"] = "eA=="
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="digest mismatch"):
        recovery_preview(root, gate, preflight)
