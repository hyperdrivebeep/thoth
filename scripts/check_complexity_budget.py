"""Complexity budget: functions already over the limit are frozen; nothing may get worse.

The limit is complexity_limit in config/complexity-budget.json (20). A function over the limit
that is not listed fails; a listed function whose complexity rose fails. A lower value or a
function that is gone (or no longer over the limit) passes and is printed as a note to lower or
remove its list entry (--update does only that and never raises or adds a value; --init writes the
first list once). Function length has its own budget in check_module_budget.py.

Measurement
- Python (src/thoth): McCabe complexity as ruff C901 computes it (if/elif, for, while, except
  handlers, a non-empty try-else, match cases, and a nested function counts 1 plus its body in its
  parent while also being measured itself). It was compared with ruff on every function of src.
- Web (apps/web/src, test files excluded): ESLint's built-in "complexity" rule, run through the
  ESLint API with only that rule (node and the web dependencies are required).

Identification is by path and name, never by line number:
- Python: path::Class.method or path::outer.inner. A second function with the same name in the
  same scope gets "#2", "#3" in source order.
- Web: path::name, where the name comes from the function's own id, or from the variable,
  property, class member or assignment it is stored in; nested functions are joined with ".". A
  function with no name (an inline callback) is "<anonN>" under the nearest named parent, N
  counting such callbacks in source order; duplicates get "#2".
Limits: renaming or moving a function, or adding an inline callback before another in the same
parent, changes its key. The old key then reads as "gone" and the new one must stay under the limit
or be listed through a reviewed change.
"""

from __future__ import annotations

import argparse
import ast
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "config" / "complexity-budget.json"
DEFAULT_LIMIT = 20

WEB_PROBE_JS = r"""
const path = require("path");
const { createRequire } = require("module");
const [project, rootsArg, excludedArg] = process.argv.slice(-3);
const roots = JSON.parse(rootsArg);
const excluded = JSON.parse(excludedArg);
const req = createRequire(path.join(project, "package.json"));
const { ESLint } = req("eslint");
const tseslint = req("typescript-eslint");
function propName(key, computed) {
  if (!key) return null;
  if (!computed && key.type === "Identifier") return key.name;
  if (key.type === "Literal") return String(key.value);
  if (key.type === "PrivateIdentifier") return "#" + key.name;
  return null;
}
const MEMBERS = [
  "Property", "MethodDefinition", "TSAbstractMethodDefinition", "PropertyDefinition",
];
function ownName(node) {
  if (node.id && node.id.name) return node.id.name;
  const p = node.parent;
  if (!p) return null;
  if (p.type === "VariableDeclarator" && p.init === node && p.id.type === "Identifier") {
    return p.id.name;
  }
  if (MEMBERS.includes(p.type) && p.value === node) return propName(p.key, p.computed);
  if (p.type === "AssignmentExpression" && p.right === node) {
    if (p.left.type === "Identifier") return p.left.name;
    if (p.left.type === "MemberExpression" && !p.left.computed) return p.left.property.name;
  }
  if (p.type === "ExportDefaultDeclaration") return "default";
  return null;
}
const probe = { rules: { functions: {
  meta: { type: "problem", schema: [] },
  create(context) {
    const frames = [{ q: "", anon: 0, used: new Map() }];
    const enter = (node, report) => {
      const top = frames[frames.length - 1];
      let name = ownName(node);
      if (name === null) name = "<anon" + (++top.anon) + ">";
      const seen = (top.used.get(name) || 0) + 1;
      top.used.set(name, seen);
      if (seen > 1) name += "#" + seen;
      const q = top.q ? top.q + "." + name : name;
      frames.push({ q, anon: 0, used: new Map() });
      const par = node.parent;
      const member = par && par.value === node && MEMBERS.includes(par.type);
      const start = member ? par.loc.start : node.loc.start;
      const end = node.loc.end;
      const where = { q, s: [start.line, start.column + 1], e: [end.line, end.column + 1] };
      if (report) context.report({ node, message: JSON.stringify(where) });
    };
    const leave = () => { frames.pop(); };
    return {
      FunctionDeclaration: (n) => enter(n, true), "FunctionDeclaration:exit": leave,
      FunctionExpression: (n) => enter(n, true), "FunctionExpression:exit": leave,
      ArrowFunctionExpression: (n) => enter(n, true), "ArrowFunctionExpression:exit": leave,
      ClassDeclaration: (n) => enter(n, false), "ClassDeclaration:exit": leave,
      ClassExpression: (n) => enter(n, false), "ClassExpression:exit": leave,
    };
  },
} } };
(async () => {
  const eslint = new ESLint({
    cwd: project,
    overrideConfigFile: true,
    overrideConfig: [
      { ignores: excluded.map((s) => "**/*" + s) },
      {
        files: ["**/*.{ts,tsx}"],
        languageOptions: { parser: tseslint.parser },
        plugins: { probe },
        rules: { complexity: ["error", 1], "probe/functions": "error" },
      },
    ],
  });
  const results = await eslint.lintFiles(roots.map((r) => r + "/**/*.{ts,tsx}"));
  const out = { complexity: [], functions: [] };
  for (const r of results) {
    const file = path.relative(project, r.filePath).split(path.sep).join("/");
    for (const m of r.messages) {
      if (m.ruleId === "complexity") {
        const value = Number((/complexity of (\d+)/.exec(m.message) || [0, 0])[1]);
        out.complexity.push({ file, line: m.line, col: m.column, value });
      } else if (m.ruleId === "probe/functions") {
        out.functions.push({ file, ...JSON.parse(m.message) });
      }
    }
  }
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { process.stderr.write(String((e && e.stack) || e)); process.exit(3); });
"""


def _load(path: Path) -> dict[str, Any]:
    value: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("complexity budget manifest must be a JSON object")
    return cast(dict[str, Any], value)


def _statements(statements: list[ast.stmt]) -> int:
    """The decision points in a statement list, counted the way ruff's C901 counts them."""
    total = 0
    for node in statements:
        if isinstance(node, ast.If):
            total += 1 + _statements(node.body)
            rest = node.orelse
            while rest:
                if len(rest) == 1 and isinstance(rest[0], ast.If):
                    total += 1 + _statements(rest[0].body)
                    rest = rest[0].orelse
                else:
                    total += _statements(rest)
                    break
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            total += 1 + _statements(node.body) + _statements(node.orelse)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            total += _statements(node.body)
        elif isinstance(node, ast.Try):
            total += _statements(node.body) + _statements(node.orelse) + _statements(node.finalbody)
            total += 1 if node.orelse else 0
            for handler in node.handlers:
                total += 1 + _statements(handler.body)
        elif isinstance(node, ast.Match):
            for case in node.cases:
                pattern = case.pattern
                wildcard = (
                    isinstance(pattern, ast.MatchAs)
                    and pattern.name is None
                    and pattern.pattern is None
                )
                total += _statements(case.body) + (0 if wildcard else 1)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            total += 1 + _statements(node.body)
        elif isinstance(node, ast.ClassDef):
            total += _statements(node.body)
    return total


def python_functions(source: str) -> dict[str, int]:
    """Complexity of every function and method in one module, by qualified name."""
    result: dict[str, int] = {}

    def visit(parent: ast.AST, scope: str, used: dict[str, int]) -> None:
        for child in ast.iter_child_nodes(parent):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                used[child.name] = used.get(child.name, 0) + 1
                name = child.name if used[child.name] == 1 else f"{child.name}#{used[child.name]}"
                qualified = f"{scope}.{name}" if scope else name
                if not isinstance(child, ast.ClassDef):
                    result[qualified] = 1 + _statements(child.body)
                visit(child, qualified, {})
            else:
                visit(child, scope, used)

    visit(ast.parse(source), "", {})
    return result


def measure_python(root: Path, section: dict[str, Any]) -> dict[str, int]:
    found: dict[str, int] = {}
    for scan_root in cast(list[object], section["scan_roots"]):
        for path in sorted((root / str(scan_root)).rglob("*.py")):
            relative = path.relative_to(root).as_posix()
            for name, value in python_functions(path.read_text(encoding="utf-8")).items():
                found[f"{relative}::{name}"] = value
    return found


def measure_web(root: Path, section: dict[str, Any]) -> dict[str, int]:
    """Complexity of every web function with complexity above 1, by path and name."""
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("node is required to measure the web complexity (or use --python-only)")
    project_dir = str(section["project_dir"])
    project = (root / project_dir).resolve()
    completed = subprocess.run(
        [
            node,
            "-e",
            WEB_PROBE_JS,
            str(project),
            json.dumps(section["scan_roots"]),
            json.dumps(section["excluded_suffixes"]),
        ],
        cwd=project,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(f"web complexity measurement failed: {completed.stderr.strip()[:400]}")
    data = cast(dict[str, Any], json.loads(completed.stdout))
    functions: dict[str, list[dict[str, Any]]] = {}
    for item in cast(list[dict[str, Any]], data["functions"]):
        functions.setdefault(str(item["file"]), []).append(item)
    found: dict[str, int] = {}
    for message in cast(list[dict[str, Any]], data["complexity"]):
        position = (int(message["line"]), int(message["col"]))
        best: dict[str, Any] | None = None
        for item in functions.get(str(message["file"]), []):
            start, end = tuple(item["s"]), tuple(item["e"])
            if start <= position < end and (
                best is None or (start >= tuple(best["s"]) and end <= tuple(best["e"]))
            ):
                best = item
        if best is None:
            raise RuntimeError(f"web function not identified: {message['file']}:{position}")
        key = f"{project_dir}/{message['file']}::{best['q']}"
        if key in found:
            raise RuntimeError(f"web function key is not unique: {key}")
        found[key] = int(message["value"])
    return found


def compare(
    label: str, measured: dict[str, int], listed: dict[str, int], limit: int
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    notes: list[str] = []
    over = {key for key, value in measured.items() if value > limit}
    for key in sorted(over):
        value = measured[key]
        if key not in listed:
            errors.append(f"{label}: new function over {limit}: {key}={value}")
        elif value > listed[key]:
            errors.append(f"{label}: complexity grew: {key} listed={listed[key]} actual={value}")
        elif value < listed[key]:
            notes.append(f"{label}: lower the list value: {key} {listed[key]} -> {value}")
    for key in sorted(set(listed) - over):
        state = "no longer over the limit" if key in measured else "function gone"
        notes.append(f"{label}: remove from the list ({state}): {key}")
    return errors, notes


def _sections(manifest: dict[str, Any], python_only: bool) -> dict[str, dict[str, Any]]:
    sections = {"python": cast(dict[str, Any], manifest["python"])}
    if "web" in manifest and not python_only:
        sections["web"] = cast(dict[str, Any], manifest["web"])
    return sections


def _measure(root: Path, name: str, section: dict[str, Any]) -> dict[str, int]:
    return measure_python(root, section) if name == "python" else measure_web(root, section)


def build_report(
    root: Path, manifest: dict[str, Any], python_only: bool
) -> tuple[dict[str, object], dict[str, dict[str, int]]]:
    limit = int(manifest["complexity_limit"])
    errors: list[str] = []
    notes: list[str] = []
    measured_all: dict[str, dict[str, int]] = {}
    counts: dict[str, object] = {}
    for name, section in _sections(manifest, python_only).items():
        listed = {str(k): int(v) for k, v in cast(dict[str, int], section["functions"]).items()}
        try:
            measured = _measure(root, name, section)
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
            errors.append(f"{name}: {exc}")
            continue
        measured_all[name] = measured
        section_errors, section_notes = compare(name, measured, listed, limit)
        errors.extend(section_errors)
        notes.extend(section_notes)
        over = sum(1 for value in measured.values() if value > limit)
        counts[name] = {"listed": len(listed), "over_limit": over}
    if python_only and "web" in manifest:
        counts["web"] = "SKIPPED"
    report: dict[str, object] = {
        "verdict": "PASS" if not errors else "FAIL",
        "complexity_limit": limit,
        "counts": counts,
        "errors": errors,
        "notes": notes,
    }
    return report, measured_all


def _write(path: Path, manifest: dict[str, Any]) -> None:
    for section in ("python", "web"):
        if section in manifest:
            functions = cast(dict[str, int], manifest[section]["functions"])
            manifest[section]["functions"] = dict(sorted(functions.items()))
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    path.write_bytes(text.encode("utf-8"))


def _initial_manifest(root: Path, python_only: bool) -> tuple[dict[str, Any], dict[str, int]]:
    manifest: dict[str, Any] = {
        "schema_version": "1.0.0",
        "complexity_limit": DEFAULT_LIMIT,
        "policy": (
            "Existing functions over the limit are frozen at the listed value. A new function "
            "over the limit, or a listed one that rises, fails the check. Lower or remove entries "
            "with --update; never raise a value."
        ),
        "python": {"scan_roots": ["src/thoth"], "functions": {}},
    }
    if not python_only:
        manifest["web"] = {
            "project_dir": "apps/web",
            "scan_roots": ["src"],
            "excluded_suffixes": [".test.ts", ".test.tsx"],
            "functions": {},
        }
    counts: dict[str, int] = {}
    for name, section in _sections(manifest, python_only).items():
        over = {k: v for k, v in _measure(root, name, section).items() if v > DEFAULT_LIMIT}
        section["functions"] = over
        counts[name] = len(over)
    return manifest, counts


def emit(value: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Complexity budget (see the file header).")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--python-only", action="store_true", help="skip the web measurement")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--init", action="store_true", help="write the first list, only once")
    mode.add_argument("--update", action="store_true", help="lower or remove entries only")
    args = parser.parse_args()
    root = args.root.resolve()
    manifest_path = args.manifest.resolve()

    if args.init:
        if manifest_path.exists():
            emit({"verdict": "FAIL", "errors": ["--init: the list already exists"]})
            return 1
        initial, counts = _initial_manifest(root, args.python_only)
        _write(manifest_path, initial)
        emit({"verdict": "PASS", "initialized": counts})
        return 0

    manifest = _load(manifest_path)
    report, measured = build_report(root, manifest, args.python_only)
    if args.update and report["verdict"] == "PASS":
        limit = int(manifest["complexity_limit"])
        changed = 0
        for name, values in measured.items():
            functions = cast(dict[str, int], manifest[name]["functions"])
            for key in list(functions):
                now = values.get(key)
                if now is None or now <= limit:
                    del functions[key]
                    changed += 1
                elif now < functions[key]:
                    functions[key] = now
                    changed += 1
        _write(manifest_path, manifest)
        report["updated_entries"] = changed
    emit(report)
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
