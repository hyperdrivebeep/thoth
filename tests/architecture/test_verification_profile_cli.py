"""Real small native routes; non-pytest stages are explicit fixture stubs, not product PASS."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from scripts.verification_progress import ROOT, start_run
from tests.architecture.native_makefile_fixture import (
    native_makefile_source,
    powershell_executable,
)

STUBS = """
function Invoke-ArchitectureVerify { Invoke-NativeStage "ARCHITECTURE" $Python @("-c", "pass") }
function Invoke-BackendLint { Invoke-NativeStage "LINT" $Python @("-c", "pass") }
function Invoke-BackendType { Invoke-NativeStage "TYPE" $Python @("-c", "pass") }
function Invoke-WebVerify {
    foreach ($stage in @("WEB_LINT", "WEB_TYPE", "WEB_TEST", "WEB_BUILD")) {
        Invoke-NativeStage $stage $Python @("-c", "pass")
    }
}
"""


@pytest.mark.parametrize("task,failure", [("tooling", False), ("verify", False), ("tooling", True)])
def test_real_makefile_profile_routes_and_pytest_failure(
    tmp_path: Path, task: str, failure: bool
) -> None:
    target = tmp_path / "tests/architecture/test_tool.py"
    target.parent.mkdir(parents=True)
    target.write_text(
        "def test_tool():\n    assert " + ("False" if failure else "True") + "\n", encoding="utf-8"
    )
    product = tmp_path / "tests/integration/test_product.py"
    product.parent.mkdir(parents=True)
    product.write_text("def test_product():\n    assert True\n", encoding="utf-8")
    directory, run = start_run(ROOT, kind="PYTEST_ONLY")
    source = (ROOT / "Makefile.ps1").read_text(encoding="utf-8")
    source = native_makefile_source(source, ROOT)
    prefix, switch = source.split("switch ($Task)", 1)
    script = tmp_path / "profile-route.ps1"
    script.write_text(prefix + STUBS + "\nswitch ($Task)" + switch, encoding="utf-8")
    environment = {
        **os.environ,
        "PYTHONPATH": str(ROOT),
        "THOTH_VERIFICATION_RUN_ID": run["run_id"],
    }
    for key in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS"):
        environment.pop(key, None)
    result = subprocess.run(
        [powershell_executable(), "-NoProfile", "-NonInteractive", "-File", str(script), task],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    stages = json.loads((directory / "stages.json").read_text())
    progress = json.loads((directory / "progress.json").read_text())
    expected = (
        ["ARCHITECTURE", "LINT", "TYPE", "TOOLING"]
        if task == "tooling"
        else [
            "ARCHITECTURE",
            "LINT",
            "TYPE",
            "BACKEND",
            "WEB_LINT",
            "WEB_TYPE",
            "WEB_TEST",
            "WEB_BUILD",
            "DOCTOR",
        ]
    )
    assert [s["name"] for s in stages["completed"]] == expected, result.stdout + result.stderr
    assert progress["total"] == (1 if task == "tooling" else 2)
    assert progress["completed"] == progress["total"]
    assert progress["counts"]["failed"] == int(failure)
    assert progress["exit_code"] == int(failure)
    assert (result.returncode != 0) is failure
    assert stages["completed"][-1]["exit_code"] == int(failure)
    assert "THOTH_VERIFY_STAGE " + expected[3] in result.stdout
    assert not (directory / "verification.json").exists()
