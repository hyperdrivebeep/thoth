from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "config" / "module-responsibility-budget.json"


def _load(path: Path) -> dict[str, Any]:
    value: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("module budget manifest must be a JSON object")
    return cast(dict[str, Any], value)


def _module_symbols(tree: ast.Module) -> tuple[str, ...]:
    return tuple(
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    )


def _integer(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    return value


def _long_functions(path: Path, tree: ast.Module, limit: int) -> dict[str, int]:
    result: dict[str, int] = {}
    relative = path.as_posix()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.end_lineno is None:
            continue
        lines = node.end_lineno - node.lineno + 1
        if lines > limit:
            result[f"{relative}::{node.name}"] = lines
    return result


def build_report(root: Path, manifest_path: Path) -> dict[str, object]:
    manifest = _load(manifest_path)
    module_limit = int(manifest["module_line_limit"])
    function_limit = int(manifest["function_line_limit"])
    watched = cast(dict[str, dict[str, object]], manifest["watched_modules"])
    expected_long = {
        str(key): _integer(value, f"watched_long_functions.{key}")
        for key, value in cast(dict[str, object], manifest["watched_long_functions"]).items()
    }
    errors: list[str] = []
    seen_modules: set[str] = set()
    actual_long: dict[str, int] = {}

    for scan_root in cast(list[object], manifest["scan_roots"]):
        base = root / str(scan_root)
        for path in sorted(base.rglob("*.py")):
            relative = path.relative_to(root).as_posix()
            text = path.read_text(encoding="utf-8")
            lines = len(text.splitlines())
            tree = ast.parse(text, filename=str(path))
            actual_long.update(_long_functions(Path(relative), tree, function_limit))
            if lines > module_limit and relative not in watched:
                errors.append(f"new oversized module is not ratcheted: {relative}={lines}")
            if relative not in watched:
                continue
            seen_modules.add(relative)
            definition = watched[relative]
            baseline = _integer(definition["baseline_lines"], f"{relative}.baseline_lines")
            if lines != baseline:
                direction = "grew" if lines > baseline else "shrank"
                errors.append(
                    f"watched module {direction}; update/refactor the ratchet: "
                    f"{relative} actual={lines} baseline={baseline}"
                )
            expected_symbols = tuple(
                str(value) for value in cast(list[object], definition["top_level_symbols"])
            )
            symbols = _module_symbols(tree)
            if symbols != expected_symbols:
                errors.append(
                    f"top-level responsibility changed in {relative}: "
                    f"actual={list(symbols)} expected={list(expected_symbols)}"
                )

    missing_modules = sorted(set(watched) - seen_modules)
    if missing_modules:
        errors.append(
            "watched modules missing; update ratchet after extraction: "
            f"{missing_modules}"
        )

    if actual_long != expected_long:
        new_keys = sorted(set(actual_long) - set(expected_long))
        removed_keys = sorted(set(expected_long) - set(actual_long))
        changed = sorted(
            key
            for key in set(actual_long).intersection(expected_long)
            if actual_long[key] != expected_long[key]
        )
        errors.append(
            "long-function ratchet changed: "
            f"new={new_keys} removed={removed_keys} changed={changed}; "
            f"remove retired entries at or below {function_limit} lines; "
            "lower baselines for still-watched shrinking functions, never raise limits"
        )

    return {
        "verdict": "PASS" if not errors else "FAIL",
        "module_line_limit": module_limit,
        "function_line_limit": function_limit,
        "watched_module_count": len(watched),
        "watched_long_function_count": len(expected_long),
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()
    root = args.root.resolve()
    manifest = args.manifest.resolve()
    report = build_report(root, manifest)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
