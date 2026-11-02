from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "config" / "architecture-conformance.json"


def load_manifest(root: Path | None = None) -> dict[str, Any]:
    path = MANIFEST if root is None else root / "config/architecture-conformance.json"
    value: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("architecture manifest must be an object")
    return cast(dict[str, Any], value)


def rule_bundle_digest(manifest: dict[str, Any], root: Path | None = None) -> str:
    base = ROOT if root is None else root
    digest = hashlib.sha256()
    for relative in rule_bundle_paths(manifest, base):
        digest.update(relative.encode())
        digest.update((base / relative).read_bytes())
    return digest.hexdigest()


def rule_bundle_paths(manifest: dict[str, Any], root: Path | None = None) -> tuple[str, ...]:
    base = ROOT if root is None else root
    # Fingerprint the active product checks and contracts; legacy automation is retired.
    guard_files = {
        "Makefile.ps1",
        "config/module-responsibility-budget.json",
        "scripts/architecture_contract.py",
        "scripts/required_architecture_checks.py",
        "scripts/extension_binding_contract.py",
        "docs/architecture/verification-profiles.md",
        ".codex/hooks.json",
        "config/atomicity-requirements.json",
        "config/atomicity-paths.json",
        "scripts/build_atomicity_path_inventory.py",
        "scripts/atomicity_call_graph.py",
        "scripts/atomicity_invocation.py",
        "scripts/atomicity_observer.py",
        "scripts/atomicity_phase_evidence.py",
        "scripts/atomicity_discovery_evidence.py",
        "scripts/run_atomicity_verification.py",
    }
    guard_files.update(
        path.relative_to(base).as_posix() for path in (base / "scripts").glob("check_*.py")
    )
    return tuple(
        dict.fromkeys(
            [
                "config/architecture-conformance.json",
                *map(str, manifest["rule_documents"]),
                *sorted(guard_files),
            ]
        )
    )


def python_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def layer_violations(manifest: dict[str, Any]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for _layer, definition in manifest["layers"].items():
        forbidden = tuple(str(item) for item in definition["forbidden_import_prefixes"])
        for root in definition["roots"]:
            for path in sorted((ROOT / str(root)).rglob("*.py")):
                blocked = sorted(
                    name for name in python_imports(path) if name.startswith(forbidden)
                )
                if blocked:
                    relative = path.relative_to(ROOT).as_posix()
                    result[relative] = blocked
    return result


def exceptions_for(manifest: dict[str, Any], rule: str) -> dict[str, dict[str, Any]]:
    return {
        str(item["path"]): item for item in manifest["known_exceptions"] if item["rule"] == rule
    }


def pattern_paths(root: Path, glob: str, pattern: str) -> set[str]:
    result: set[str] = set()
    for path in root.glob(glob):
        if pattern in path.read_text(encoding="utf-8"):
            result.add(path.relative_to(ROOT).as_posix())
    return result


def validate_rule_documents(manifest: dict[str, Any]) -> list[str]:
    return [str(path) for path in manifest["rule_documents"] if not (ROOT / str(path)).is_file()]


def known_exception_ids(manifest: dict[str, Any]) -> set[str]:
    return {str(item["id"]) for item in manifest["known_exceptions"]}


def acceptance_ids(manifest: dict[str, Any]) -> set[str]:
    return {str(item["id"]) for item in manifest["acceptance_contracts"]}
