"""Source-bound evidence is required before a tracked owner can lose its OPEN debt."""

from __future__ import annotations

import argparse
import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

try:
    from scripts.atomicity_discovery_evidence import discovery_errors
    from scripts.atomicity_invocation import receipt_errors
    from scripts.atomicity_phase_evidence import phase_errors
except ModuleNotFoundError:
    from atomicity_discovery_evidence import discovery_errors
    from atomicity_invocation import receipt_errors
    from atomicity_phase_evidence import phase_errors

ROOT = Path(__file__).resolve().parents[1]
OWNERS = frozenset(
    {
        "PROJECT_GOVERNANCE",
        "SOURCE_ARTIFACT_SPAN",
        "EVIDENCE_OBSERVATION_CLAIM",
        "CRITERION",
        "DECISION_OBJECT",
        "HYPOTHESIS",
        "ACTION_PLAN_AUTHORIZATION",
        "EXECUTION_OUTCOME",
        "IMPROVEMENT",
        "CLOSURE_EXPORT",
    }
)


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def local_path(root: Path, relative: str) -> Path:
    result = (root / relative).resolve()
    if not result.is_relative_to(root.resolve()):
        raise ValueError("atomicity evidence path escaped repository")
    return result


def load(root: Path, relative: str) -> Any:
    return json.loads(local_path(root, relative).read_text(encoding="utf-8-sig"))


def contract_digest(requirements: dict[str, Any], paths: dict[str, Any]) -> str:
    required = {
        **requirements,
        "owners": [
            {k: v for k, v in owner.items() if k not in {"closure_status", "evidence"}}
            for owner in requirements["owners"]
        ],
    }
    inventory = {k: v for k, v in paths.items() if k not in {"closure_ready", "blockers"}}
    inventory["phases"] = [
        {k: v for k, v in phase.items() if k not in {"disposition", "evidence_refs"}}
        for phase in paths["phases"]
    ]
    return digest({"requirements": required, "paths": inventory})


def source_manifest(root: Path) -> list[dict[str, str]]:
    files: set[Path] = set()
    for folder in ("src", "tests", "migrations", "scripts"):
        files.update(p for p in (root / folder).rglob("*.py") if "__pycache__" not in p.parts)
    for folder in ("schemas", "config"):
        files.update(
            p
            for p in (root / folder).rglob("*")
            if p.is_file()
            and p.suffix in {".json", ".yaml", ".yml", ".toml"}
            and not p.name.startswith("atomicity-")
            and p.name != "architecture-conformance.json"
        )
    for name in ("pyproject.toml", "uv.lock", "conftest.py", "Makefile.ps1", ".codex/hooks.json"):
        if (root / name).is_file():
            files.add(root / name)
    result = [
        {
            "path": p.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        }
        for p in sorted(files)
    ]
    manifest = load(root, "config/architecture-conformance.json")
    manifest["canonical_owners"] = [
        {
            k: v
            for k, v in owner.items()
            if k not in {"current_atomicity", "atomicity_debt", "atomicity_evidence"}
        }
        for owner in manifest["canonical_owners"]
    ]
    result.append({"path": "@architecture-structure", "sha256": digest(manifest)})
    return result


def passed_nodes(path: Path) -> set[str]:
    document = ET.parse(path).getroot()
    suites = list(document.iter("testsuite"))
    if not suites or any(
        int(s.get(k, "0")) for s in suites for k in ("failures", "errors", "skipped")
    ):
        raise ValueError("atomicity evidence contains failed, skipped or missing suites")
    nodes: set[str] = set()
    for case in document.iter("testcase"):
        if any(case.find(kind) is not None for kind in ("failure", "error", "skipped")):
            raise ValueError("atomicity testcase did not pass")
        nodes.add(case.attrib["classname"].replace(".", "/") + ".py::" + case.attrib["name"])
    if not nodes:
        raise ValueError("atomicity evidence has no test cases")
    return nodes


def coverage_errors(
    root: Path, manifest: dict[str, Any] | None = None, *, require_complete: bool = False
) -> list[str]:
    errors: list[str] = []
    manifest = load(root, "config/architecture-conformance.json") if manifest is None else manifest
    requirements = load(root, "config/atomicity-requirements.json")
    paths = load(root, "config/atomicity-paths.json")
    required = {row["owner"]: row for row in requirements.get("owners", [])}
    if set(required) != OWNERS or len(requirements["owners"]) != len(OWNERS):
        errors.append("atomicity owner requirement set differs from the ten tracked owners")
    catalog = load(root, "schemas/protocol/public-method-catalog.json")
    expected_methods = {row["name"] for row in catalog["methods"]}
    entries = paths.get("public_entries", [])
    if {row["name"] for row in entries} != expected_methods or len(entries) != len(
        expected_methods
    ):
        errors.append("atomicity inventory omits or duplicates a public/query/control entry")
    catalog_hash = hashlib.sha256(
        local_path(root, "schemas/protocol/public-method-catalog.json").read_bytes()
    ).hexdigest()
    if paths.get("catalog_sha256") != catalog_hash:
        errors.append("atomicity public catalog fingerprint changed")
    phases = {row["path_id"]: row for row in paths.get("phases", [])}
    shared_consumers = {
        "INVESTIGATION_LEDGER",
        "RECEIPT_DAG",
        "PROJECTPACK_MANIFEST",
        "SEMANTIC_REVISION_LEDGER",
        "OPERATION_JOURNAL",
    }
    if len(phases) != len(paths.get("phases", [])):
        errors.append("atomicity phase IDs are duplicated")
    for entry in entries:
        if not entry.get("phase_ids") or not set(entry["phase_ids"]).issubset(phases):
            errors.append(f"atomicity phase binding missing: {entry['name']}")
    for phase in phases.values():
        if not all(
            phase.get(field)
            for field in (
                "actual_callable",
                "caller_refs",
                "phase_id",
                "expected_basis",
                "allowed_phase_deltas",
                "downstream_reader",
            )
        ):
            errors.append(f"atomicity phase contract incomplete: {phase['path_id']}")
    for method in catalog["methods"]:
        if method["canonical_owner"] not in OWNERS | shared_consumers:
            continue
        entry = next((row for row in entries if row["name"] == method["name"]), {})
        if not any(
            phases.get(key, {}).get("owner") in OWNERS for key in entry.get("phase_ids", [])
        ):
            errors.append(
                f"atomicity owner/shared consumer was classified out of closure: {method['name']}"
            )
    declared = {row["aggregate"]: row for row in manifest["canonical_owners"]}
    closing = [
        owner for owner in OWNERS if declared.get(owner, {}).get("current_atomicity") == "ATOMIC"
    ]
    # Shared routes publish across several owners. This migration supports only the
    # full ten-owner closure; a primary display owner is not a dependency boundary.
    if closing and set(closing) != OWNERS:
        errors.append(
            "atomicity partial owner closure is unsupported; all ten owners must close together"
        )
    if require_complete and set(closing) != OWNERS:
        errors.append("tracked owner closure is incomplete")
    if not closing and not require_complete:
        return errors
    evidence_path = requirements.get("evidence_file")
    if not isinstance(evidence_path, str):
        return [*errors, "closed atomicity owner lacks source-bound execution evidence"]
    try:
        errors.extend(discovery_errors(root, paths))
        evidence = load(root, evidence_path)
        if evidence.get("contract_digest") != contract_digest(requirements, paths):
            errors.append("atomicity path/owner evidence contract changed")
        current_source = source_manifest(root)
        if evidence.get("source_files") != current_source or evidence.get(
            "source_digest"
        ) != digest(current_source):
            errors.append("atomicity evidence source fingerprint changed")
        xml = local_path(root, evidence["junit_file"])
        if hashlib.sha256(xml.read_bytes()).hexdigest() != evidence.get("junit_sha256"):
            errors.append("atomicity JUnit bytes differ from recorded evidence")
        nodes = passed_nodes(xml)
        collection = load(root, evidence["collection_file"])
        if digest(collection) != evidence.get("collection_digest") or set(collection) != nodes:
            errors.append("atomicity collected and passed node IDs differ")
        errors.extend(receipt_errors(root, requirements, evidence, collection))
        result_file = local_path(root, evidence["result_file"])
        if hashlib.sha256(result_file.read_bytes()).hexdigest() != evidence.get("result_sha256"):
            errors.append("atomicity result receipt bytes changed")
        result = load(root, evidence["result_file"])
        if any(evidence.get(k) != v for k, v in result.items()):
            errors.append("atomicity evidence and execution result receipt differ")
        errors.extend(receipt_errors(root, requirements, result, collection))
        observation_file = local_path(root, evidence["observations_file"])
        if hashlib.sha256(observation_file.read_bytes()).hexdigest() != evidence.get(
            "observations_sha256"
        ):
            errors.append("atomicity callable observation bytes changed")
        observations = load(root, evidence["observations_file"])
        if observations.get("schema_version") != 1 or observations.get("exit_code") != 0:
            errors.append("atomicity callable observations do not record a valid completed run")
        if set(observations.get("nodes", {})) != nodes:
            errors.append("atomicity callable observation and passing node sets differ")
        if not paths.get("closure_ready") or paths.get("blockers"):
            errors.append("atomicity inventory declares incomplete closure or unresolved blockers")
        for owner in closing:
            contract = required[owner]
            if (
                contract.get("closure_status") != "VERIFIED"
                or declared[owner].get("atomicity_debt") is not None
            ):
                errors.append(f"atomicity closure/history state mismatch: {owner}")
            if declared[owner].get("atomicity_evidence") != evidence_path:
                errors.append(f"atomicity owner does not reference its evidence: {owner}")
            files = contract["test_files"] + requirements["common_tests"]
            if any(not any(node.startswith(name + "::") for node in nodes) for name in files):
                errors.append(f"atomicity required owner test file has no passing cases: {owner}")
            owner_phases = [phase for phase in phases.values() if phase.get("owner") == owner]
            if not owner_phases:
                errors.append(f"atomicity owner has missing or unverified required phases: {owner}")
            for phase in owner_phases:
                errors.extend(phase_errors(root, phase, nodes, observations.get("nodes", {})))
    except (OSError, ValueError, KeyError, TypeError, ET.ParseError) as exc:
        errors.append(f"atomicity evidence invalid: {type(exc).__name__}: {exc}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    try:
        errors = coverage_errors(ROOT, require_complete=args.require_complete)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errors = [str(exc)]
    print(
        json.dumps({"verdict": "PASS" if not errors else "FAIL", "errors": errors}, sort_keys=True)
    )
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
