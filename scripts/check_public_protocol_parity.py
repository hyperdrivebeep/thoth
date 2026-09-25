from __future__ import annotations

import json
from pathlib import Path

from audit_full_product_coverage import parse_catalog

from thoth.protocol.notifications import IMPLEMENTED_NOTIFICATIONS
from thoth.protocol.registry import PUBLIC_METHODS

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT if (ROOT / "PROJECT_WIKI").is_dir() else ROOT.parent
MANIFEST = ROOT / "schemas/protocol/public-method-catalog.json"
WIKI = WORKSPACE / "PROJECT_WIKI/30_ARCHITECTURE/rpc-method-catalog.md"


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entries = {item["name"]: item for item in manifest["methods"]}
    runtime = set(PUBLIC_METHODS)
    manifest_methods = set(entries)
    wiki = {
        row["name"]
        for row in parse_catalog(WIKI.read_text(encoding="utf-8"))
        if row["surface"] in {"QUERY", "COMMAND", "QUERY_OR_CONTROL"}
    }
    canonical = {name for name, item in entries.items() if item["canonical"]}
    errors: list[str] = []
    undeclared = sorted(runtime - manifest_methods)
    missing_runtime = sorted(manifest_methods - runtime)
    missing_documented = sorted(canonical - wiki)
    if undeclared:
        errors.append("undeclared runtime methods")
    if missing_runtime:
        errors.append("manifest methods missing at runtime")
    if missing_documented:
        errors.append("canonical methods missing from wiki")
    if set(manifest["notifications"]) != set(IMPLEMENTED_NOTIFICATIONS):
        errors.append("notification manifest drift")
    if any(
        not item.get("canonical_owner")
        or not item.get("policy")
        or not item.get("normal_entrypoint")
        or not item.get("behavioral_evidence")
        for item in entries.values()
    ):
        errors.append("method metadata incomplete")
    payload = {
        "verdict": "PASS" if not errors else "FAIL",
        "errors": errors,
        "undeclared_runtime_methods": undeclared,
        "missing_runtime_methods": missing_runtime,
        "missing_documented_methods": missing_documented,
        "runtime_method_count": len(runtime),
        "canonical_method_count": len(canonical),
        "alias_count": len(manifest_methods - canonical),
        "notification_count": len(IMPLEMENTED_NOTIFICATIONS),
    }
    print(json.dumps(payload, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
