from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import cast

from thoth.protocol.notifications import IMPLEMENTED_NOTIFICATIONS
from thoth.protocol.registry import PUBLIC_METHODS

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT if (ROOT / "PROJECT_WIKI").is_dir() else ROOT.parent
CATALOG = WORKSPACE / "PROJECT_WIKI" / "30_ARCHITECTURE" / "rpc-method-catalog.md"
METHOD_MANIFEST = ROOT / "schemas" / "protocol" / "public-method-catalog.json"
JSON_OUT = ROOT / "artifacts" / "qa" / "full-product-coverage.json"
MARKDOWN_OUT = ROOT / "docs" / "verification" / "full-product-coverage.md"
CSV_OUT = ROOT / "artifacts" / "qa" / "full-product-method-ledger.csv"

METHOD = re.compile(r"^[a-z][A-Za-z0-9]*(?:/[A-Za-z][A-Za-z0-9]*)+$")
NAMESPACE = re.compile(r"^##\s+\d+\.\s+(.+?)\s+Namespace")
SURFACE_BY_HEADING = {
    "query": "QUERY",
    "queries": "QUERY",
    "public query": "QUERY",
    "public queries": "QUERY",
    "command": "COMMAND",
    "commands": "COMMAND",
    "public command": "COMMAND",
    "public commands": "COMMAND",
    "notification": "NOTIFICATION",
    "notifications": "NOTIFICATION",
    "public notification": "NOTIFICATION",
    "public notifications": "NOTIFICATION",
}
PHASE_BY_NAMESPACE = {
    "project": "F1",
    "thread": "F1",
    "operation": "F1",
    "investigation": "F2",
    "evidence": "F2",
    "criteria": "F2",
    "object": "F2",
    "hypothesis": "F3",
    "action": "F3",
    "execution": "F3",
    "revision": "F4",
    "outcome": "F4",
    "memory": "F5",
    "improvement": "F5",
    "receipt": "F6",
    "closure": "F6",
    "export": "F6",
}
COMPATIBILITY_ALIASES = {
    "project/delete": "project/archive",
    "action/preflight/read": "action/authorization/prepare",
    "closure/finalize": "closure/decide",
    "export/prepare": "export/plan/create",
    "revision/compare": "revision/diff/read",
    "revision/history/read": "revision/list",
}


def parse_catalog(text: str) -> list[dict[str, str]]:
    namespace = "operation"
    surface = ""
    fenced = False
    shared_operation_block = False
    rows: list[dict[str, str]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        namespace_match = NAMESPACE.match(line)
        if namespace_match:
            namespace = namespace_match.group(1).split()[0].lower()
            surface = ""
        if line.startswith("### "):
            surface = SURFACE_BY_HEADING.get(line.removeprefix("### ").lower(), "")
        if line == "Shared query/control methods:":
            shared_operation_block = True
            namespace = "operation"
            surface = "QUERY_OR_CONTROL"
        if line.startswith("```"):
            fenced = not fenced
            if not fenced:
                shared_operation_block = False
            continue
        if not fenced or not surface:
            continue
        if METHOD.fullmatch(line):
            rows.append(
                {
                    "name": line,
                    "namespace": namespace,
                    "surface": surface,
                }
            )
        if shared_operation_block and not fenced:
            surface = ""
    unique = {row["name"]: row for row in rows}
    return [unique[name] for name in sorted(unique)]


def main() -> None:
    method_manifest = cast(
        dict[str, object],
        json.loads(METHOD_MANIFEST.read_text(encoding="utf-8")),
    )
    manifest_methods = cast(list[dict[str, object]], method_manifest["methods"])
    manifest_notifications = tuple(
        str(item) for item in cast(list[object], method_manifest["notifications"])
    )
    canonical_entries = tuple(item for item in manifest_methods if item["canonical"] is True)
    catalog_rows = [
        {
            "name": str(item["name"]),
            "namespace": str(item["namespace"]),
            "surface": str(item["surface"]),
        }
        for item in canonical_entries
    ]
    runtime = set(PUBLIC_METHODS)
    callable_catalog = {
        row["name"]
        for row in catalog_rows
        if row["surface"] in {"QUERY", "COMMAND", "QUERY_OR_CONTROL"}
    }
    implemented = callable_catalog & runtime
    declared_runtime = {str(item["name"]) for item in manifest_methods}
    undeclared_runtime = runtime - declared_runtime
    missing = callable_catalog - runtime
    by_namespace: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "callable_designed": 0,
            "callable_implemented": 0,
            "notifications_designed": 0,
            "notifications_implemented": 0,
        }
    )
    for row in catalog_rows:
        values = by_namespace[row["namespace"]]
        if row["surface"] == "NOTIFICATION":
            values["notifications_designed"] += 1
            if row["name"] in IMPLEMENTED_NOTIFICATIONS:
                values["notifications_implemented"] += 1
        else:
            values["callable_designed"] += 1
            if row["name"] in runtime:
                values["callable_implemented"] += 1
    method_ledger: list[dict[str, str]] = [
        {
            **row,
            "phase": PHASE_BY_NAMESPACE.get(row["namespace"], "F0"),
            "module": f"src/thoth/application/commands/{row['namespace']}.py",
            "status": ("IMPLEMENTED" if row["name"] in runtime else "MISSING"),
            "required_evidence": (
                "typed_schema|persistence_or_projection|handler|contract_test|integration_test"
            ),
        }
        for row in catalog_rows
    ]
    implemented_notifications = len(set(manifest_notifications) & IMPLEMENTED_NOTIFICATIONS)
    coverage_ratio = round(
        len(implemented) / len(callable_catalog) if callable_catalog else 1.0,
        6,
    )
    payload: dict[str, object] = {
        "catalog_path": str(CATALOG.relative_to(WORKSPACE)).replace("\\", "/"),
        "designed_callable_count": len(callable_catalog),
        "designed_notification_count": len(manifest_notifications),
        "implemented_callable_count": len(implemented),
        "implemented_notification_count": implemented_notifications,
        "runtime_public_count": len(runtime),
        "missing_callable_count": len(missing),
        "undeclared_runtime_count": len(undeclared_runtime),
        "coverage_ratio": coverage_ratio,
        "implemented": sorted(implemented),
        "missing": sorted(missing),
        "undeclared_runtime": sorted(undeclared_runtime),
        "notifications": sorted(manifest_notifications),
        "missing_notifications": sorted(set(manifest_notifications) - IMPLEMENTED_NOTIFICATIONS),
        "by_namespace": dict(sorted(by_namespace.items())),
        "compatibility_aliases": {
            str(item["name"]): str(item["alias_target"])
            for item in manifest_methods
            if item["canonical"] is False
        },
        "method_ledger": method_ledger,
    }
    JSON_OUT.parent.mkdir(parents=True, exist_ok=True)
    MARKDOWN_OUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    with CSV_OUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "name",
                "namespace",
                "surface",
                "phase",
                "module",
                "status",
                "required_evidence",
            ),
        )
        writer.writeheader()
        writer.writerows(method_ledger)  # pyright: ignore[reportArgumentType]
    lines = [
        "# THOTH Full Product Backend Coverage",
        "",
        "This compares the full canonical RPC catalog with the active runtime registry. "
        "It is a materialization ledger, not a product-completeness claim.",
        "",
        f"- Designed callable methods: **{len(callable_catalog)}**",
        f"- Designed notifications: **{len(manifest_notifications)}**",
        f"- Implemented catalog methods: **{len(implemented)}**",
        f"- Active runtime methods: **{len(runtime)}**",
        f"- Implemented notifications: **{implemented_notifications}**",
        f"- Missing callable methods: **{len(missing)}**",
        f"- Coverage: **{coverage_ratio:.1%}**",
        "",
        "## Namespace coverage",
        "",
        "| Namespace | Callable designed | Implemented | Notifications designed | Implemented |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, values in sorted(by_namespace.items()):
        lines.append(
            f"| {name} | {values['callable_designed']} | "
            f"{values['callable_implemented']} | {values['notifications_designed']} | "
            f"{values['notifications_implemented']} |"
        )
    lines.extend(["", "## Runtime methods outside the full catalog", ""])
    lines.extend(f"- `{name}`" for name in sorted(undeclared_runtime))
    lines.extend(["", "## Missing callable methods", ""])
    lines.extend(f"- `{name}`" for name in sorted(missing))
    lines.extend(["", "## Materialization phases", ""])
    for phase in ("F1", "F2", "F3", "F4", "F5", "F6"):
        phase_rows = [row for row in method_ledger if row["phase"] == phase]
        phase_missing = [row for row in phase_rows if row["status"] == "MISSING"]
        lines.append(
            f"- **{phase}**: {len(phase_rows) - len(phase_missing)}/{len(phase_rows)} "
            f"catalog entries materialized"
        )
    MARKDOWN_OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "designed_callable": len(callable_catalog),
                "designed_notifications": len(manifest_notifications),
                "implemented": len(implemented),
                "implemented_notifications": implemented_notifications,
                "missing": len(missing),
                "coverage_ratio": payload["coverage_ratio"],
            }
        )
    )


if __name__ == "__main__":
    main()
