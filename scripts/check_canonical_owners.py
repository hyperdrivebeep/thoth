from __future__ import annotations

import json
from typing import cast

from architecture_contract import ROOT, load_manifest
from check_atomicity_coverage import OWNERS, coverage_errors


def main() -> int:
    manifest = load_manifest()
    errors: list[str] = []
    aggregates: set[str] = set()
    owner_scopes: set[tuple[str, str]] = set()
    debts: set[str] = set()
    acceptance_ids = {str(item["id"]) for item in manifest["acceptance_contracts"]}
    allowed_atomicity = {"ATOMIC", "PARTIAL", "MATERIALIZED_ONLY", "DESIGNED"}

    for item in manifest["canonical_owners"]:
        aggregate = str(item.get("aggregate", ""))
        owner = str(item.get("canonical_owner", ""))
        scope = str(item.get("canonical_scope", aggregate))
        projections = item.get("projections")
        uow = str(item.get("unit_of_work_target", ""))
        atomicity = str(item.get("current_atomicity", ""))
        if not aggregate or aggregate in aggregates:
            errors.append(f"canonical aggregate is missing or duplicated: {aggregate!r}")
        aggregates.add(aggregate)
        key = (owner, scope)
        if not owner or key in owner_scopes:
            errors.append(f"canonical owner/scope is missing or duplicated: {key}")
        owner_scopes.add(key)
        typed_projections = cast(list[object], projections) if isinstance(projections, list) else []
        if not isinstance(projections, list) or len(typed_projections) != len(
            {str(value) for value in typed_projections}
        ):
            errors.append(f"projection list is invalid for {aggregate}")
        if not uow:
            errors.append(f"Unit of Work target is missing for {aggregate}")
        if atomicity not in allowed_atomicity:
            errors.append(f"invalid atomicity state for {aggregate}: {atomicity}")
        raw_debt = item.get("atomicity_debt")
        if atomicity == "ATOMIC":
            if raw_debt is not None:
                errors.append(f"ATOMIC owner must not retain atomicity debt: {aggregate}")
            continue
        if not isinstance(raw_debt, dict):
            errors.append(f"non-atomic owner lacks explicit debt contract: {aggregate}")
            continue
        debt = cast(dict[str, object], raw_debt)
        debt_id = str(debt.get("debt_id", ""))
        if not debt_id or debt_id in debts:
            errors.append(f"atomicity debt id is missing or duplicated: {aggregate}")
        debts.add(debt_id)
        if debt.get("status") != "OPEN":
            errors.append(f"non-atomic debt must remain OPEN until verified: {aggregate}")
        raw_acceptances = debt.get("acceptance_ids")
        debt_acceptances: set[str] = (
            {str(value) for value in cast(list[object], raw_acceptances)}
            if isinstance(raw_acceptances, list)
            else set[str]()
        )
        if not debt_acceptances or not debt_acceptances.issubset(acceptance_ids):
            errors.append(f"atomicity debt has invalid Acceptance ownership: {aggregate}")
        if not str(debt.get("closure_evidence_required", "")).strip():
            errors.append(f"atomicity debt lacks closure evidence: {aggregate}")

    if (ROOT / "config/atomicity-requirements.json").exists() or any(
        item.get("aggregate") in OWNERS and item.get("current_atomicity") == "ATOMIC"
        for item in manifest["canonical_owners"]
    ):
        try:
            errors.extend(coverage_errors(ROOT, manifest))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append(f"atomicity evidence contract unavailable: {exc}")

    payload = {
        "verdict": "PASS" if not errors else "FAIL",
        "aggregate_count": len(aggregates),
        "atomicity_debt_count": len(debts),
        "fully_atomic": len(debts) == 0,
        "errors": errors,
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
