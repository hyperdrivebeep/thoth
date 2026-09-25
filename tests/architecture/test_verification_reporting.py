from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from uuid import uuid4

import pytest
from scripts.verification_progress import ROOT as PROGRESS_ROOT
from scripts.verification_progress import running_status, start_run

ROOT = Path(__file__).resolve().parents[2]


def run_suite(tmp_path: Path, source: str, *, enabled: bool = True, precreated: bool = False):
    target = tmp_path / "test_sample.py"
    target.write_text(source, encoding="utf-8")
    run_id = "vr-" + uuid4().hex
    if enabled and precreated:
        start_run(ROOT, kind="PYTEST_ONLY", run_id=run_id)
    arguments = [
        sys.executable,
        "-m",
        "pytest",
        str(target),
        "--rootdir",
        str(tmp_path),
        "-q",
        "--tb=no",
        "--show-capture=no",
        "--no-summary",
        "-rN",
        "--disable-warnings",
    ]
    if enabled:
        arguments += ["-p", "scripts.pytest_progress", "--thoth-progress-run", run_id]
    environment = {**os.environ}
    environment.pop("THOTH_VERIFICATION_RUN_ID", None)
    started = time.perf_counter()
    completed = subprocess.run(
        arguments,
        cwd=ROOT,
        env=environment,
        capture_output=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    directory = ROOT / ".thoth/verification-runs" / run_id
    if enabled:
        (directory / "observer-wall.json").write_text(
            json.dumps({"seconds": time.perf_counter() - started}), encoding="utf-8"
        )
    return completed, directory


def test_reporter_counts_cases_once_and_preserves_teardown_failure(tmp_path: Path) -> None:
    result, directory = run_suite(
        tmp_path,
        """
import time
import pytest
@pytest.fixture
def slow():
    time.sleep(0.03)
    yield
    assert False
def test_pass():
    assert True
def test_teardown(slow):
    time.sleep(0.04)
""",
    )
    assert result.returncode == 1
    progress = json.loads((directory / "progress.json").read_text(encoding="utf-8"))
    assert progress["completed"] == progress["total"] == 2
    assert progress["counts"]["passed"] == 1
    assert progress["counts"]["failed"] == 1
    assert progress["session_finished"] is True
    durations = json.loads((directory / "durations.json").read_text(encoding="utf-8"))
    slow = next(item for item in durations["cases"] if item["function"] == "test_teardown")
    assert slow["phases"]["setup"]["duration_s"] >= 0.02
    assert slow["phases"]["call"]["duration_s"] >= 0.03
    assert slow["phases"]["teardown"]["outcome"] == "failed"


def test_reporting_redacts_parameters_capture_and_locals(tmp_path: Path) -> None:
    secret = "FAKE_PRIVATE_PARAMETER_9af41"
    result, directory = run_suite(
        tmp_path,
        f'''
import pytest
@pytest.mark.parametrize("value", ["{secret}"], ids=["{secret}"])
def test_failure(value):
    private_local = value
    print(private_local)
    assert private_local == "different"
''',
    )
    assert result.returncode == 1
    assert secret not in result.stdout and secret not in result.stderr
    for name in ("run.json", "progress.json", "durations.json", "junit-safe.xml"):
        assert secret not in (directory / name).read_text(encoding="utf-8")
    xml = ET.parse(directory / "junit-safe.xml").getroot()
    assert len(xml.findall("testcase")) == 1
    assert xml.find("testcase/failure") is not None


def test_skip_xfail_and_xpass_keep_pytest_exit_and_case_counts(tmp_path: Path) -> None:
    result, directory = run_suite(
        tmp_path,
        """
import pytest
def test_pass(): pass
@pytest.mark.skip(reason="not selected by fixture policy")
def test_skip(): pass
@pytest.mark.xfail
def test_xfail(): assert False
@pytest.mark.xfail
def test_xpass(): pass
@pytest.mark.xfail(strict=True)
def test_strict_xpass(): pass
""",
    )
    assert result.returncode == 1
    progress = json.loads((directory / "progress.json").read_text())
    assert progress["completed"] == progress["total"] == 5
    assert progress["counts"] == {
        "passed": 1,
        "failed": 0,
        "skipped": 1,
        "xfailed": 1,
        "xpassed": 1,
        "xpassed_strict": 1,
    }


@pytest.mark.parametrize(
    "source,kind",
    [
        ("def invalid syntax", "collection"),
        ("def test_interrupt():\n    raise KeyboardInterrupt()\n", "interrupt"),
    ],
)
def test_collection_error_and_interrupt_do_not_count_unfinished_pass(
    tmp_path: Path, source: str, kind: str
) -> None:
    result, directory = run_suite(tmp_path, source)
    assert result.returncode == 2
    progress = json.loads((directory / "progress.json").read_text())
    assert progress["completed"] == 0 and progress["counts"]["passed"] == 0
    assert progress["exit_code"] == 2
    assert bool(progress["collection_errors"]) == (kind == "collection")


def test_report_write_failure_does_not_change_test_exit(tmp_path: Path) -> None:
    (tmp_path / "conftest.py").write_text(
        """
def pytest_configure(config):
    import scripts.pytest_progress as reporter
    def broken(*args, **kwargs):
        raise OSError("FAKE_PRIVATE_WRITER_ERROR")
    reporter.atomic_json = broken
""",
        encoding="utf-8",
    )
    result, directory = run_suite(tmp_path, "def test_pass(): pass\n")
    assert result.returncode == 0
    assert "FAKE_PRIVATE_WRITER_ERROR" not in result.stdout + result.stderr
    assert "TELEMETRY_UNAVAILABLE" in result.stdout
    observed = running_status(PROGRESS_ROOT, directory.name)
    assert observed["status"] == "TELEMETRY_UNAVAILABLE"
    assert observed["sealed"] is False


def test_enabled_and_disabled_keep_same_execution_order_and_outcomes(tmp_path: Path) -> None:
    source = """
from pathlib import Path
import pytest
import time
@pytest.fixture(autouse=True)
def record(request):
    with (Path(__file__).parent / "order.txt").open("a") as f:
        f.write(request.node.originalname + "\\n")
def test_pass(): time.sleep(0.01)
def test_failure(): assert False
@pytest.mark.xfail
def test_expected(): assert False
"""
    times: dict[str, list[float]] = {"enabled": [], "disabled": []}
    orders: list[str] = []
    result_codes: list[int] = []
    for repeat in range(3):
        for enabled in [False, True] if repeat % 2 == 0 else [True, False]:
            directory = tmp_path / f"{repeat}-{enabled}"
            directory.mkdir()
            started = time.perf_counter()
            result, report_dir = run_suite(directory, source, enabled=enabled, precreated=True)
            elapsed = time.perf_counter() - started
            if enabled:
                elapsed = json.loads((report_dir / "observer-wall.json").read_text())["seconds"]
            times["enabled" if enabled else "disabled"].append(elapsed)
            result_codes.append(result.returncode)
            orders.append((directory / "order.txt").read_text())
    assert result_codes == [1] * 6
    assert all(order == "test_pass\ntest_failure\ntest_expected\n" for order in orders)
    evidence = ROOT / ".thoth/verification-runs/observer-overhead.json"
    evidence.write_text(
        json.dumps(
            {
                "source": "small identical isolated suite",
                "samples": times,
                "outcome_codes": result_codes,
                "same_order": True,
                "not_a_whole_suite_speed_claim": True,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
