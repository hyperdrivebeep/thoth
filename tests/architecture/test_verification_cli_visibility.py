from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

from scripts.verification_progress import ROOT, run_directory, start_run
from tests.architecture.native_makefile_fixture import (
    native_makefile_source,
    powershell_executable,
)


def test_real_running_watch_reports_slow_phase_without_starting_tests(tmp_path: Path) -> None:
    target = tmp_path / "test_visible.py"
    target.write_text(
        """
import pytest
import time
@pytest.fixture
def slow_setup():
    time.sleep(1.5)
def test_visible(slow_setup):
    time.sleep(1.5)
""",
        encoding="utf-8",
    )
    run_id = "vr-" + uuid4().hex
    args = [
        sys.executable,
        "-m",
        "pytest",
        str(target),
        "--rootdir",
        str(tmp_path),
        "-p",
        "scripts.pytest_progress",
        "--thoth-progress-run",
        run_id,
        "-q",
        "--tb=no",
        "--no-summary",
        "-rN",
    ]
    environment = {**os.environ}
    environment.pop("THOTH_VERIFICATION_RUN_ID", None)
    with subprocess.Popen(
        args, cwd=ROOT, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    ) as child:
        directory = run_directory(ROOT, run_id)
        deadline = time.monotonic() + 10
        while not (directory / "progress.json").exists() and time.monotonic() < deadline:
            assert child.poll() is None
            time.sleep(0.02)
        assert (directory / "progress.json").exists()
        observed = subprocess.run(
            [
                sys.executable,
                "scripts/verification_status.py",
                "running",
                "--run-id",
                run_id,
                "--watch",
                "--interval",
                "0.1",
                "--samples",
                "20",
                "--long-after",
                "0.1",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        stdout, stderr = child.communicate(timeout=15)
    assert child.returncode == 0, stdout + stderr
    assert observed.returncode == 0, observed.stderr
    states = [json.loads(line) for line in observed.stdout.splitlines() if line.strip()]
    assert any(state.get("pytest", {}).get("current") for state in states), states
    assert all(state["sealed"] is False for state in states)
    (directory / "watch-demo.json").write_text(
        json.dumps(states, ensure_ascii=False), encoding="utf-8"
    )


def test_makefile_stage_records_real_native_failure(tmp_path: Path) -> None:
    directory, run = start_run(ROOT, kind="PYTEST_ONLY")
    source = (ROOT / "Makefile.ps1").read_text(encoding="utf-8")
    prefix = source.split("switch ($Task)", 1)[0]
    prefix = native_makefile_source(prefix, ROOT)
    script = tmp_path / "stage-probe.ps1"
    script.write_text(
        prefix + '\nInvoke-NativeStage "BACKEND" $Python @("-c", "raise SystemExit(7)")\n',
        encoding="utf-8",
    )
    result = subprocess.run(
        [powershell_executable(), "-NoProfile", "-File", str(script)],
        cwd=ROOT,
        env={**os.environ, "THOTH_VERIFICATION_RUN_ID": run["run_id"]},
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode != 0
    stages = json.loads((directory / "stages.json").read_text())
    assert stages["completed"], result.stdout + result.stderr
    assert stages["completed"][0]["exit_code"] == 7
    assert stages["completed"][0]["state"] == "FAILED"
    assert stages["completed"][0]["elapsed_s"] >= 0
