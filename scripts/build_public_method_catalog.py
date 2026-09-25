from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast

from audit_full_product_coverage import COMPATIBILITY_ALIASES, parse_catalog

from thoth.protocol.notifications import IMPLEMENTED_NOTIFICATIONS
from thoth.protocol.registry import PUBLIC_METHODS

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT if (ROOT / "PROJECT_WIKI").is_dir() else ROOT.parent
WIKI = WORKSPACE / "PROJECT_WIKI/30_ARCHITECTURE/rpc-method-catalog.md"
OUTPUT = ROOT / "schemas/protocol/public-method-catalog.json"

EXTENSIONS = {
    "project/archive": ("project", "COMMAND", "A01"),
    "revision/timeline/read": ("revision", "QUERY", "A07"),
    "revision/timeline/item/read": ("revision", "QUERY", "A07"),
    "thread/result/read": ("thread", "QUERY", "A01"),
    "thread/result/compare/read": ("thread", "QUERY", "A01"),
    "project/review/list": ("project", "QUERY", "A01"),
    "revision/restore/apply": ("revision", "COMMAND", "A07"),
    "revision/restore": ("revision", "COMMAND", "A07"),
    "improvement/evaluation/run": ("improvement", "COMMAND", "A08"),
    "improvement/evaluation/result/read": ("improvement", "QUERY", "A08"),
    "improvement/exposure/runtime/read": ("improvement", "QUERY", "A08"),
    "improvement/exposure/start": ("improvement", "COMMAND", "A08"),
    "improvement/exposure/decide": ("improvement", "COMMAND", "A08"),
    "improvement/exposure/complete": ("improvement", "COMMAND", "A08"),
    "improvement/exposure/rollback": ("improvement", "COMMAND", "A08"),
    "project/source/scope/read": ("project", "QUERY", "A13"),
    "project/source/scope/grant": ("project", "COMMAND", "A13"),
    "project/source/scope/revoke": ("project", "COMMAND", "A13"),
    "project/source/scope/assign": ("project", "COMMAND", "A13"),
    "project/source/scope/update": ("project", "COMMAND", "A13"),
    "projectpack/list": ("projectpack", "QUERY", "A01"),
    "projectpack/run": ("projectpack", "COMMAND", "A01"),
    "field/protocol/seal": ("field", "COMMAND", "A12"),
    "field/session/start": ("field", "COMMAND", "A12"),
    "field/event/record": ("field", "COMMAND", "A12"),
    "field/session/end": ("field", "COMMAND", "A12"),
    "field/score/record": ("field", "COMMAND", "A12"),
    "field/export/build": ("field", "COMMAND", "A12"),
}

OWNER = {
    "model": "THREAD_REQUEST_SETTINGS",
    "workspace": "THREAD_REQUEST_SETTINGS",
    "project": "PROJECT_GOVERNANCE",
    "thread": "DECISION_OBJECT",
    "operation": "OPERATION_JOURNAL",
    "investigation": "INVESTIGATION_LEDGER",
    "evidence": "EVIDENCE_OBSERVATION_CLAIM",
    "criteria": "CRITERION",
    "object": "DECISION_OBJECT",
    "hypothesis": "HYPOTHESIS",
    "action": "ACTION_PLAN_AUTHORIZATION",
    "execution": "EXECUTION_OUTCOME",
    "revision": "SEMANTIC_REVISION_LEDGER",
    "outcome": "EXECUTION_OUTCOME",
    "memory": "MEMORY",
    "improvement": "IMPROVEMENT",
    "receipt": "RECEIPT_DAG",
    "closure": "CLOSURE_EXPORT",
    "export": "CLOSURE_EXPORT",
    "projectpack": "PROJECTPACK_MANIFEST",
    "field": "FIELD_MEASUREMENT_LEDGER",
}

ACCEPTANCE = {
    "model": "U08,U07",
    "workspace": "U08",
    "project": "A01",
    "thread": "A01",
    "operation": "A01",
    "investigation": "A02",
    "evidence": "A01,A02,A03",
    "criteria": "A09",
    "object": "A01",
    "hypothesis": "A01,A03",
    "action": "A04,A05",
    "execution": "A04,A05",
    "revision": "A07",
    "outcome": "A04,A05",
    "memory": "A06",
    "improvement": "A08",
    "receipt": "A10",
    "closure": "A10",
    "export": "A10",
    "projectpack": "A01",
    "field": "A12",
}

ENTRYPOINT = {
    "model": "thread/input",
    "workspace": "thread/input",
    "project": "project/create",
    "thread": "thread/start|thread/input",
    "operation": "normal JSON-RPC dispatch",
    "investigation": "thread/start|thread/input",
    "evidence": "project/source/connect|thread/input",
    "criteria": "thread/input|criteria/compile",
    "object": "thread/start|thread/input",
    "hypothesis": "thread/input",
    "action": "thread/input",
    "execution": "thread/input|execution/start",
    "revision": "thread/input|revision/changeSet/commit",
    "outcome": "thread/input",
    "memory": "thread/input",
    "improvement": (
        "thread/input|improvement/propose|improvement/evaluation/plan|improvement/evaluation/run"
    ),
    "receipt": "thread/input|closure/prepare",
    "closure": "closure/prepare",
    "export": "export/plan/create",
    "projectpack": "projectpack/run",
    "field": "field/protocol/seal|normal RPC fieldSessionId",
}


def _method_entry(
    *,
    name: str,
    namespace: str,
    surface: str,
    canonical: bool,
    alias_target: str | None,
    evidence: str | None = None,
) -> dict[str, object]:
    return {
        "name": name,
        "namespace": namespace,
        "surface": surface,
        "canonical_owner": OWNER[namespace],
        "policy": "research-model-settings:versioned"
        if namespace in {"model", "workspace"}
        else f"{namespace}:versioned-policy",
        "normal_entrypoint": ENTRYPOINT[namespace],
        "behavioral_evidence": tuple((evidence or ACCEPTANCE[namespace]).split(",")),
        "canonical": canonical,
        "alias_target": alias_target,
        "runtime_status": "IMPLEMENTED",
    }


def build() -> dict[str, object]:
    rows = parse_catalog(WIKI.read_text(encoding="utf-8"))
    wiki_methods = {
        row["name"]: row
        for row in rows
        if row["surface"] in {"QUERY", "COMMAND", "QUERY_OR_CONTROL"}
    }
    methods: list[dict[str, object]] = []
    for name, row in sorted(wiki_methods.items()):
        if name in COMPATIBILITY_ALIASES or name in EXTENSIONS:
            continue
        # Sections can document cross-namespace methods. The method owns its namespace.
        namespace = name.split("/", 1)[0]
        methods.append(
            _method_entry(
                name=name,
                namespace=namespace,
                surface=str(row["surface"]),
                canonical=True,
                alias_target=None,
            )
        )
    for name, (namespace, surface, evidence) in sorted(EXTENSIONS.items()):
        if name not in {item["name"] for item in methods}:
            methods.append(
                _method_entry(
                    name=name,
                    namespace=namespace,
                    surface=surface,
                    canonical=True,
                    alias_target=None,
                    evidence=evidence,
                )
            )
    by_name = {str(item["name"]): item for item in methods}
    for alias, target in sorted(COMPATIBILITY_ALIASES.items()):
        target_entry = by_name[target]
        target_evidence = tuple(
            str(item)
            for item in cast(tuple[object, ...], target_entry["behavioral_evidence"])
            if isinstance(item, str)
        )
        methods.append(
            _method_entry(
                name=alias,
                namespace=alias.split("/", 1)[0],
                surface=str(target_entry["surface"]),
                canonical=False,
                alias_target=target,
                evidence=",".join(target_evidence),
            )
        )
    methods.sort(key=lambda item: str(item["name"]))
    runtime = set(PUBLIC_METHODS)
    if {str(item["name"]) for item in methods} != runtime:
        raise ValueError("manifest builder inputs do not match PUBLIC_METHODS")
    unsigned: dict[str, object] = {
        "schema_version": "1.0.0",
        "authority": "PROJECT_WIKI/30_ARCHITECTURE/rpc-method-catalog.md",
        "canonical_method_count": sum(bool(item["canonical"]) for item in methods),
        "compatibility_alias_count": sum(not bool(item["canonical"]) for item in methods),
        "runtime_method_count": len(methods),
        "notification_count": len(IMPLEMENTED_NOTIFICATIONS),
        "methods": methods,
        "notifications": sorted(IMPLEMENTED_NOTIFICATIONS),
    }
    digest = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {**unsigned, "catalog_digest": digest}


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(build(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
