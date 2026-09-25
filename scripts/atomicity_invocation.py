"""An explicit, source-bound pytest invocation for owner evidence, independent of shell filters."""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from typing import Any

PLUGINS = ["pytest_asyncio.plugin", "scripts.atomicity_observer"]
ENVIRONMENT = {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
REMOVED_PREFIXES = ("PYTEST_", "COV_CORE_", "COVERAGE_")


def environment(root: Path) -> dict[str, str]:
    result = {k: v for k, v in os.environ.items() if not k.upper().startswith(REMOVED_PREFIXES)}
    result.update(ENVIRONMENT)
    result["PYTHONPATH"] = os.pathsep.join((str(root), str(root / "src")))
    return result


def test_files(requirements: dict[str, Any]) -> list[str]:
    return sorted(
        {name for owner in requirements["owners"] for name in owner["test_files"]}
        | set(requirements["common_tests"])
    )


def command(root: Path, requirements: dict[str, Any], interpreter: str) -> list[str]:
    return [
        interpreter,
        "-m",
        "pytest",
        "-c",
        str(root / "pyproject.toml"),
        "--rootdir",
        str(root),
        "--confcutdir",
        str(root),
        *[arg for plugin in PLUGINS for arg in ("-p", plugin)],
        *test_files(requirements),
    ]


def identity(root: Path) -> dict[str, Any]:
    executable = Path(sys.executable).resolve()
    return {
        "interpreter": str(executable),
        "interpreter_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "python_version": list(sys.version_info[:3]),
        "config": "pyproject.toml",
        "config_sha256": hashlib.sha256((root / "pyproject.toml").read_bytes()).hexdigest(),
        "plugins": PLUGINS,
        "environment": ENVIRONMENT,
        "removed_environment_prefixes": list(REMOVED_PREFIXES),
        "pythonpath": [str(root), str(root / "src")],
    }


def receipt_errors(
    root: Path, requirements: dict[str, Any], receipt: dict[str, Any], nodes: list[str]
) -> list[str]:
    errors: list[str] = []
    if type(receipt.get("exit_code")) is not int or receipt["exit_code"] != 0:
        errors.append("atomicity receipt does not record exit 0")
    if receipt.get("source_unchanged") is not True:
        errors.append("atomicity receipt does not establish unchanged source")
    if type(receipt.get("collected")) is not int or receipt["collected"] != len(nodes):
        errors.append("atomicity receipt collection count mismatch")
    invocation = receipt.get("invocation")
    if invocation != identity(root):
        errors.append("atomicity pytest interpreter/config/environment identity mismatch")
    base = command(root, requirements, str(Path(sys.executable).resolve()))
    if receipt.get("command") != base:
        errors.append("atomicity pytest command differs from the declared complete file selection")
    expected_collect = [*base, "--collect-only"]
    if receipt.get("collection_command") != expected_collect:
        errors.append("atomicity collection invocation mismatch")
    expected_run = [
        *base,
        "-vv",
        f"--junitxml={root / receipt.get('junit_file', '')}",
        f"--atomicity-observations={root / receipt.get('observations_file', '')}",
    ]
    if receipt.get("execution_command") != expected_run:
        errors.append("atomicity execution invocation mismatch")
    required = requirements.get("required_nodes")
    if not isinstance(required, list) or not required or required != sorted(set(required)):
        errors.append("atomicity required node contract is absent or duplicated")
    elif sorted(nodes) != required:
        errors.append("atomicity required node contract differs from collection")
    return errors
