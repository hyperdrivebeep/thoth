from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_every_non_atomic_owner_has_acceptance_bound_debt() -> None:
    manifest = json.loads(
        (ROOT / "config/architecture-conformance.json").read_text(encoding="utf-8")
    )
    acceptances = {item["id"] for item in manifest["acceptance_contracts"]}
    debts: set[str] = set()
    for owner in manifest["canonical_owners"]:
        if owner["current_atomicity"] == "ATOMIC":
            assert "atomicity_debt" not in owner
            continue
        debt = owner["atomicity_debt"]
        assert debt["status"] == "OPEN"
        assert debt["debt_id"] not in debts
        assert set(debt["acceptance_ids"]).issubset(acceptances)
        assert debt["closure_evidence_required"]
        debts.add(debt["debt_id"])

    assert len(debts) == sum(
        owner["current_atomicity"] != "ATOMIC" for owner in manifest["canonical_owners"]
    )


def test_atomicity_debt_is_not_described_as_full_namespace_completion() -> None:
    matrix = (ROOT / "PROJECT_WIKI/50_SEED_ROADMAP/implementation-maturity-matrix.md").read_text(
        encoding="utf-8"
    )
    manifest = json.loads((ROOT / "config/architecture-conformance.json").read_text())
    count = sum(
        owner.get("atomicity_debt", {}).get("status") == "OPEN"
        for owner in manifest["canonical_owners"]
    )
    assert f"ATOMICITY DEBT: {count} OPEN" in matrix
    assert "bounded slice D4/D5 does not certify full namespace atomicity" in matrix
