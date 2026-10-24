"""Report initial type debt, then enforce a reviewed native basedpyright baseline in CI."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Callable
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

Runner = Callable[[list[str], Path], subprocess.CompletedProcess[str]]
BASELINE = Path("config/basedpyright-baseline.json")


def execute(command: list[str], root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=3300
    )


def baseline_entries(raw: bytes) -> Counter[str]:
    value = json.loads(raw)
    if not isinstance(value, dict) or not isinstance(value.get("files"), dict):
        raise ValueError("Invalid native baseline: files must be an object")
    entries: Counter[str] = Counter()
    for path, items in value["files"].items():
        if not isinstance(path, str) or not isinstance(items, list):
            raise ValueError("Invalid native baseline file entry")
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("range"), dict):
                raise ValueError("Invalid native baseline diagnostic")
            bounds = item["range"]
            for field in ("startColumn", "endColumn"):
                if type(bounds.get(field)) is not int or bounds[field] < 0:
                    raise ValueError("Invalid native baseline column")
            if "lineCount" in bounds and (
                type(bounds["lineCount"]) is not int or bounds["lineCount"] < 1
            ):
                raise ValueError("Invalid native baseline line count")
            if "code" in item and not isinstance(item["code"], str):
                raise ValueError("Invalid native baseline diagnostic rule")
            entries[json.dumps([path, item], sort_keys=True)] += 1
    return entries


def diagnostic_report(raw: str, returncode: int) -> dict[str, Any]:
    if returncode not in (0, 1):
        raise ValueError(f"Checker failed with exit code {returncode}")
    # 1.39.10 prints this baseline-update notice before JSON even with --outputjson.
    # Preserve raw stdout and accept only that known notice, not arbitrary log prefixes.
    notice = re.compile(
        r"\Aupdated [^\r\n]+ with \d+ errors? "
        r"\((?:went (?:up|down) by \d+|error count didn't change)\)\r?\n"
    )
    value = json.loads(notice.sub("", raw, count=1))
    summary = value.get("summary") if isinstance(value, dict) else None
    diagnostics = value.get("generalDiagnostics") if isinstance(value, dict) else None
    if not isinstance(summary, dict) or not isinstance(diagnostics, list):
        raise ValueError("Missing checker JSON diagnostics or summary")
    if type(summary.get("filesAnalyzed")) is not int or summary["filesAnalyzed"] <= 0:
        raise ValueError("Checker did not analyze any files")
    for severity, key in (("error", "errorCount"), ("warning", "warningCount")):
        count = sum(isinstance(d, dict) and d.get("severity") == severity for d in diagnostics)
        if type(summary.get(key)) is not int or summary[key] != count:
            raise ValueError(f"Inconsistent checker {key}")
    if returncode == 0 and summary["errorCount"]:
        raise ValueError("Checker returned success with errors")
    if returncode == 1 and not (summary["errorCount"] or summary["warningCount"]):
        raise ValueError("Checker failed without error or warning diagnostics")
    for diagnostic in diagnostics:
        if (
            not isinstance(diagnostic, dict)
            or not isinstance(diagnostic.get("message"), str)
            or not isinstance(diagnostic.get("file"), str)
            or diagnostic.get("severity") not in {"error", "warning", "information"}
        ):
            raise ValueError("Invalid checker diagnostic")
        span = diagnostic.get("range")
        start = span.get("start") if isinstance(span, dict) else None
        if not isinstance(start, dict) or type(start.get("line")) is not int or start["line"] < 0:
            raise ValueError("Invalid checker diagnostic location")
    return value


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", "<br>")


def markdown(result: dict[str, Any], root: Path) -> str:
    lines = ["## Python type check", "", f"Mode: **{result['mode']}**", ""]
    if result["mode"] == "REPORT_ONLY":
        lines.append("No reviewed baseline yet. Diagnostics are reported, not a type-check pass.")
    lines.extend(
        [f"Result: **{result['status']}**", f"Baseline diagnostics: {result['baseline_count']}"]
    )
    report = result.get("diagnostics")
    if report:
        counts = report["summary"]
        label = "Reported" if result["mode"] == "REPORT_ONLY" else "New/unbaselined"
        lines.append(
            f"{label}: {counts['errorCount']} errors, {counts['warningCount']} warnings; "
            f"{counts['filesAnalyzed']} files analyzed."
        )
        lines.extend(["", "| File:line | Rule | Diagnostic |", "| --- | --- | --- |"])
        diagnostics = report["generalDiagnostics"]
        for diagnostic in diagnostics[:100]:
            path = Path(diagnostic.get("file", "unknown"))
            try:
                display = path.resolve().relative_to(root.resolve()).as_posix()
            except ValueError:
                display = path.name
            line = diagnostic.get("range", {}).get("start", {}).get("line", 0) + 1
            lines.append(
                f"| {_cell(display)}:{line} | {_cell(diagnostic.get('rule', '—'))} | "
                f"{_cell(diagnostic['message'])} |"
            )
        if len(diagnostics) > 100:
            lines.append("Only the first 100 diagnostics are shown; the artifact contains all.")
    lines.extend(["", f"Removed baseline entries (candidate only): {result['removed_count']}"])
    if result.get("problem"):
        lines.append(f"Failure: {_cell(result['problem'])}")
    lines.append("Native baseline matching does not compare diagnostic messages.")
    return "\n".join(lines) + "\n"


def check(
    root: Path,
    output: Path,
    *,
    checker: Path,
    runner: Runner = execute,
    provenance: dict[str, Any] | None = None,
    baseline_required: bool = False,
) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    baseline = root / BASELINE
    original = baseline.read_bytes() if baseline.exists() else None
    result: dict[str, Any] = {
        "mode": "ENFORCE" if original is not None or baseline_required else "REPORT_ONLY",
        "status": "ERROR",
        "exit_code": 1,
        "baseline_count": 0,
        "removed_count": 0,
    }
    receipt = {**(provenance or {}), "commands": [], "mode": result["mode"]}
    try:
        if original is None and baseline_required:
            raise ValueError("Reviewed baseline was removed; reporting mode is not allowed")
        before = baseline_entries(original) if original is not None else Counter()
        result["baseline_count"] = before.total()
        receipt["baseline_sha256"] = hashlib.sha256(original).hexdigest() if original else None
        with TemporaryDirectory(prefix="typecheck-") as scratch:
            working = Path(scratch) / "baseline.json"
            if original is not None:
                working.write_bytes(original)
            command = [
                str(checker),
                "--project",
                str(root / "pyproject.toml"),
                "--pythonpath",
                sys.executable,
                "--baselinefile",
                str(working),
                "--baselinemode=auto",
                "--outputjson",
            ]

            def invoke(arguments: list[str], label: str) -> tuple[dict[str, Any], int]:
                receipt["commands"].append({"label": label, "argv": arguments})
                completed = runner(arguments, root)
                receipt["commands"][-1]["returncode"] = completed.returncode
                (output / f"{label}.stdout.txt").write_text(completed.stdout, encoding="utf-8")
                (output / f"{label}.stderr.txt").write_text(completed.stderr, encoding="utf-8")
                parsed = diagnostic_report(completed.stdout, completed.returncode)
                _write_json(output / f"{label}.json", parsed)
                return parsed, completed.returncode

            report, code = invoke(command, "diagnostics")
            result["diagnostics"] = report
            result["checker_exit_code"] = code
            if original is None:
                # Generation is confined to initial reporting mode, never the enforcing gate.
                generate = [arg for arg in command if not arg.startswith("--baselinemode=")]
                generated, _ = invoke([*generate, "--writebaseline"], "baseline-generation")
                del generated
                candidate = working.read_bytes()
                generated_entries = baseline_entries(candidate)
                if report["generalDiagnostics"] and not generated_entries:
                    raise ValueError("Baseline generation lost all reported diagnostics")
                (output / "baseline-candidate.json").write_bytes(candidate)
                result.update(status="REPORTED_NOT_ENFORCED", exit_code=0)
            else:
                candidate = working.read_bytes()
                after = baseline_entries(candidate)
                if after - before:
                    raise ValueError("Checker unexpectedly expanded the fixed baseline")
                result["removed_count"] = (before - after).total()
                if candidate != original:
                    (output / "baseline-reduced-candidate.json").write_bytes(candidate)
                result.update(status="PASS" if code == 0 else "FAIL", exit_code=code)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        result.update(status="ERROR", exit_code=1, problem=str(exc))
    finally:
        current = baseline.read_bytes() if baseline.exists() else None
        if current != original:
            result.update(status="ERROR", exit_code=1, problem="Tracked baseline was modified")
        _write_json(output / "provenance.json", receipt)
        _write_json(output / "result.json", result)
        (output / "summary.md").write_text(markdown(result, root), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("test-results/typecheck"))
    parser.add_argument("--markdown-output", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    try:
        head = execute(["git", "rev-parse", "HEAD"], root)
        if head.returncode:
            raise ValueError("Cannot identify the checked commit")
        required = False
        base = os.environ.get("TYPECHECK_BASE_SHA", "")
        if base and set(base) != {"0"}:
            if len(base) != 40 or any(c not in "0123456789abcdef" for c in base):
                raise ValueError("Invalid type-check base SHA")
            commit = execute(["git", "cat-file", "-e", f"{base}^{{commit}}"], root)
            if commit.returncode:
                raise ValueError("Cannot inspect the type-check base commit")
            required = (
                execute(["git", "cat-file", "-e", f"{base}:{BASELINE.as_posix()}"], root).returncode
                == 0
            )
        receipt = {
            "commit": head.stdout.strip(),
            "base_sha": base or None,
            "basedpyright_version": version("basedpyright"),
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "configuration_sha256": hashlib.sha256(
                (root / "pyproject.toml").read_bytes()
            ).hexdigest(),
            "lock_sha256": hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest(),
        }
        checker = Path(sys.executable).with_name(
            "basedpyright.exe" if os.name == "nt" else "basedpyright"
        )
        result = check(
            root, output, checker=checker, provenance=receipt, baseline_required=required
        )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        output.mkdir(parents=True, exist_ok=True)
        result = {
            "mode": "UNKNOWN",
            "status": "ERROR",
            "exit_code": 1,
            "baseline_count": 0,
            "removed_count": 0,
            "problem": str(exc),
        }
        _write_json(output / "result.json", result)
        (output / "summary.md").write_text(markdown(result, root), encoding="utf-8")
    summary = (output / "summary.md").read_text(encoding="utf-8")
    print(summary)
    if args.markdown_output:
        with args.markdown_output.open("a", encoding="utf-8") as stream:
            stream.write(summary)
    return result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
