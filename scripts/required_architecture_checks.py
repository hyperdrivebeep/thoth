"""One ordered, fail-closed architecture check contract for every gate entry point."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

CHECKS = (
    "check_architecture.py",
    "check_ocp_extensions.py",
    "check_truth_drift.py",
    "check_module_budget.py",
    "check_canonical_owners.py",
    "check_acceptance_coverage.py",
    "check_migration_discipline.py",
)


def validate_check_results(results: object) -> None:
    if not isinstance(results, list) or len(results) != len(CHECKS):
        raise ValueError("required architecture checks are missing or duplicated")
    for name, result in zip(CHECKS, results, strict=True):
        if not isinstance(result, dict) or result.get("check") != name:
            raise ValueError(f"required architecture check identity mismatch: {name}")
        if result.get("verdict") != "PASS" or result.get("errors") != []:
            raise ValueError(f"required architecture check did not pass: {name}")
        if result.get("skipped") or result.get("exit_code") != 0:
            raise ValueError(f"required architecture check was skipped or failed: {name}")


def run_checks(root: Path, *, deadline: float | None = None) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for name in CHECKS:
        remaining = 30.0 if deadline is None else min(30.0, deadline - time.monotonic())
        if remaining <= 0:
            raise TimeoutError("architecture candidate check budget exhausted")
        completed = subprocess.run(
            [sys.executable, str(root / "scripts" / name)],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=remaining,
        )
        if completed.returncode:
            raise RuntimeError(
                f"architecture check failed: {name}: {completed.stdout}{completed.stderr}"
            )
        result = json.loads(completed.stdout)
        if not isinstance(result, dict):
            raise ValueError(f"architecture check output is not an object: {name}")
        results.append({**result, "check": name, "exit_code": completed.returncode})
    validate_check_results(results)
    return results


if __name__ == "__main__":
    for result in run_checks(Path(__file__).resolve().parents[1]):
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
