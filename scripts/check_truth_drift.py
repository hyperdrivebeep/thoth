from __future__ import annotations

import json
import re
from pathlib import Path

try:
    from scripts.architecture_contract import ROOT
except ModuleNotFoundError:
    from architecture_contract import ROOT


def truth_drift_errors(root: Path) -> list[str]:
    base = root
    manifest = json.loads(
        (base / "config/architecture-conformance.json").read_text(encoding="utf-8")
    )
    now = (base / "PROJECT_WIKI/NOW.md").read_text(encoding="utf-8")
    ocp = (base / "PROJECT_WIKI/30_ARCHITECTURE/current-ocp-gaps.md").read_text(
        encoding="utf-8"
    )
    matrix = (
        base / "PROJECT_WIKI/50_SEED_ROADMAP/implementation-maturity-matrix.md"
    ).read_text(encoding="utf-8")
    extensions = {str(item["name"]): item for item in manifest["extension_points"]}
    errors: list[str] = []
    stale = (
        "OCP-CONNECTOR-001`, `OCP-SANDBOX-001`, `HERO-TRACE-001`로 OPEN",
        "최초 workspace→project 자동 binding은 OPEN",
    )
    if any(value in now for value in stale):
        errors.append("OCP-DOC-DRIFT-001: NOW reopens already closed OCP work")
    required_rules = (
        "OCP-REGISTRY-001",
        "OCP-FACTORY-001",
        "OCP-CORE-CLOSED-001",
        "OCP-DOC-DRIFT-001",
    )
    missing_rules = [value for value in required_rules if value not in ocp]
    if missing_rules:
        errors.append(f"OCP-DOC-DRIFT-001: OCP projection misses rules: {missing_rules}")
    for name in ("PARSER", "CONNECTOR", "SANDBOX"):
        if extensions.get(name, {}).get("status") != "IMPLEMENTED":
            errors.append(f"OCP-DOC-DRIFT-001: {name} registry closure is not reflected")
    owners = manifest.get("canonical_owners")
    if not isinstance(owners, list):
        errors.append("OCP-DOC-DRIFT-001: canonical owner ledger is missing")
    else:
        count = sum(item.get("atomicity_debt", {}).get("status") == "OPEN" for item in owners)
        for label, document in (("NOW", now), ("OCP", ocp), ("maturity", matrix)):
            projected = re.findall(r"^ATOMICITY DEBT: (\d+) OPEN$", document, re.MULTILINE)
            if projected != [str(count)]:
                errors.append(
                    f"OCP-DOC-DRIFT-001: {label} atomicity debt projection "
                    f"{projected} differs from {count} OPEN"
                )
    return errors


def main() -> int:
    errors = truth_drift_errors(ROOT)
    payload = {
        "verdict": "PASS" if not errors else "FAIL",
        "rules": ["OCP-DOC-DRIFT-001"],
        "errors": errors,
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
