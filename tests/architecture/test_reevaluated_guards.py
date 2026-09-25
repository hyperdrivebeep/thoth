from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from scripts import architecture_gate_contract as gate
from scripts import checkpoint_commit_contract as checkpoint
from scripts.check_ocp_extensions import core_closed_errors, extension_manifest_errors
from scripts.check_truth_drift import truth_drift_errors

ROOT = Path(__file__).resolve().parents[2]


def mirror(root: Path) -> dict[str, Any]:
    for name in ("config", "src", "PROJECT_WIKI"):
        shutil.copytree(ROOT / name, root / name, ignore=shutil.ignore_patterns("__pycache__"))
    return json.loads((root / "config/architecture-conformance.json").read_text())


def test_legitimate_atomicity_decrease_passes_but_stale_projection_fails(tmp_path: Path) -> None:
    manifest = mirror(tmp_path)
    count = sum(
        x.get("atomicity_debt", {}).get("status") == "OPEN" for x in manifest["canonical_owners"]
    )
    owner = {
        "aggregate": "FIXTURE_OWNER",
        "current_atomicity": "PARTIAL",
        "atomicity_debt": {"status": "OPEN"},
    }
    manifest["canonical_owners"].append(owner)
    for relative in (
        "PROJECT_WIKI/NOW.md",
        "PROJECT_WIKI/30_ARCHITECTURE/current-ocp-gaps.md",
        "PROJECT_WIKI/50_SEED_ROADMAP/implementation-maturity-matrix.md",
    ):
        page = tmp_path / relative
        page.write_text(
            page.read_text(encoding="utf-8").replace(
                f"\nATOMICITY DEBT: {count} OPEN\n", f"\nATOMICITY DEBT: {count + 1} OPEN\n", 1
            ),
            encoding="utf-8",
        )
    owner["current_atomicity"] = "ATOMIC"
    owner.pop("atomicity_debt")
    (tmp_path / "config/architecture-conformance.json").write_text(json.dumps(manifest))
    assert truth_drift_errors(tmp_path), "stale owner count must reject a decreased-debt manifest"
    current_marker = f"\nATOMICITY DEBT: {count + 1} OPEN\n"
    updated_marker = f"\nATOMICITY DEBT: {count} OPEN\n"
    matrix = tmp_path / "PROJECT_WIKI/50_SEED_ROADMAP/implementation-maturity-matrix.md"
    matrix.write_text(
        matrix.read_text(encoding="utf-8").replace(current_marker, updated_marker, 1),
        encoding="utf-8",
    )
    assert truth_drift_errors(tmp_path), "matrix-only update leaves NOW and OCP stale"
    for relative in (
        "PROJECT_WIKI/NOW.md",
        "PROJECT_WIKI/30_ARCHITECTURE/current-ocp-gaps.md",
    ):
        page = tmp_path / relative
        # Replace only the explicit current marker; historical prose is preserved.
        page.write_text(
            page.read_text(encoding="utf-8").replace(current_marker, updated_marker, 1),
            encoding="utf-8",
        )
    assert truth_drift_errors(tmp_path) == []


def test_factory_rejects_existing_unrelated_symbol(tmp_path: Path) -> None:
    manifest = mirror(tmp_path)
    next(x for x in manifest["extension_points"] if x["name"] == "MODEL")["factory_target"] = (
        "ClockPort"
    )
    (tmp_path / "config/architecture-conformance.json").write_text(json.dumps(manifest))
    assert extension_manifest_errors(tmp_path)


def test_unknown_vendor_branch_is_not_a_registry_extension(tmp_path: Path) -> None:
    (tmp_path / "handler.py").write_text(
        "def route(provider):\n    if provider == 'brand_new_vendor':\n        return 1\n"
    )
    assert core_closed_errors(tmp_path)


def test_mixed_staged_and_unstaged_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    def mixed_status(root: Path) -> tuple[str, ...]:
        del root
        return ("MM README.md",)

    monkeypatch.setattr(checkpoint, "_status", mixed_status)
    assert not checkpoint._fully_staged(ROOT)  # pyright: ignore[reportPrivateUsage]


def test_preflight_cannot_omit_checks_even_with_recomputed_identity() -> None:
    value = copy.deepcopy(json.loads((ROOT / ".thoth/architecture/preflight.json").read_text()))
    value["status"] = "READY_FOR_EDIT"
    value["baseline_checks"] = []
    value["preflight_receipt_id"] = gate.calculate_preflight_receipt_id(value)
    with pytest.raises(ValueError, match="check"):
        gate.validate_preflight(value, allowed_statuses=frozenset({"READY_FOR_EDIT"}))


def test_wiki_receipt_rechecks_actual_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    wiki = tmp_path / "PROJECT_WIKI/NOW.md"
    wiki.parent.mkdir()
    wiki.write_bytes(b"before")
    preflight: dict[str, object] = {"preflight_receipt_id": "a" * 64, "acceptance_id": "A11"}
    value: dict[str, object] = {
        "schema_version": "1.0.0",
        **preflight,
        "state": "UPDATED",
        "updated_paths": [
            {
                "path": "PROJECT_WIKI/NOW.md",
                "size": 6,
                "sha256": hashlib.sha256(b"before").hexdigest(),
            }
        ],
        "no_change_reason": None,
        "recorded_at": "2026-09-05",
    }
    value["wiki_sync_receipt_id"] = gate.calculate_wiki_sync_receipt_id(value)
    gate.validate_wiki_sync(value, preflight=preflight)
    wiki.write_bytes(b"after!")
    with pytest.raises(ValueError, match="wiki"):
        gate.validate_wiki_sync(value, preflight=preflight)
