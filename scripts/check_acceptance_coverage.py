from __future__ import annotations

import json
import re

from architecture_contract import ROOT, acceptance_ids, load_manifest

LEVEL = {f"D{index}": index for index in range(7)}


def main() -> int:
    manifest = load_manifest()
    errors: list[str] = []
    expected = {f"A{index:02d}" for index in range(1, 14)}
    actual = acceptance_ids(manifest)
    if actual != expected:
        errors.append(
            f"acceptance set mismatch: actual={sorted(actual)} expected={sorted(expected)}"
        )

    wiki_root = ROOT / "PROJECT_WIKI"
    if not wiki_root.is_dir():
        wiki_root = ROOT.parent / "PROJECT_WIKI"
    contract_path = wiki_root / "50_SEED_ROADMAP/behavioral-acceptance-contracts.md"
    matrix_path = wiki_root / "50_SEED_ROADMAP/implementation-maturity-matrix.md"
    for path in (contract_path, matrix_path):
        if not path.is_file():
            errors.append(f"required acceptance document is missing: {path}")

    contract_text = contract_path.read_text(encoding="utf-8") if contract_path.is_file() else ""
    headings = set(re.findall(r"^## (A\d{2})\b", contract_text, flags=re.MULTILINE))
    if headings != expected:
        errors.append(f"acceptance heading mismatch: actual={sorted(headings)}")

    for item in manifest["acceptance_contracts"]:
        current = str(item["current_level"])
        target = str(item["target_level"])
        if current not in LEVEL or target not in LEVEL or LEVEL[target] < LEVEL[current]:
            errors.append(f"invalid maturity transition for {item['id']}: {current}->{target}")

    blockers: dict[str, list[str]] = {identifier: [] for identifier in expected}
    for exception in manifest["known_exceptions"]:
        for identifier in exception["blocks_acceptance"]:
            if identifier not in expected:
                errors.append(f"exception {exception['id']} blocks unknown acceptance {identifier}")
            else:
                blockers[identifier].append(str(exception["id"]))

    payload = {
        "verdict": "PASS" if not errors else "FAIL",
        "acceptance_count": len(actual),
        "blockers": {key: sorted(value) for key, value in blockers.items() if value},
        "errors": errors,
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
