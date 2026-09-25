"""Optional reporting-only pytest plugin; no selection, result or execution changes."""

from __future__ import annotations

import hashlib
import json
import os
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

from scripts.verification_progress import ROOT, atomic_json, process_identity, read_json, run_directory, start_run, utc_now


def pytest_addoption(parser: Any) -> None:
    parser.addoption("--thoth-progress-run", default=None, help="Opaque local observation run ID")


def pytest_configure(config: Any) -> None:
    run_id = config.getoption("thoth_progress_run") or os.environ.get("THOTH_VERIFICATION_RUN_ID")
    if not run_id:
        return
    try:
        directory = run_directory(ROOT, run_id, create=True)
        if not (directory / "run.json").exists():
            start_run(ROOT, kind="PYTEST_ONLY", run_id=run_id)
        owner = directory / "backend-owner.json"
        with owner.open("x", encoding="utf-8") as handle:
            json.dump({"run_id": run_id, "process": process_identity(os.getpid())}, handle)
        run = read_json(directory / "run.json")
        if run["kind"] == "PYTEST_ONLY":
            run.update(process=process_identity(os.getpid()), started_at=utc_now())
            atomic_json(directory / "run.json", run)
        config.pluginmanager.register(ProgressReporter(directory, run_id, config.rootpath), "thoth-progress-reporter")
    except Exception:
        print("THOTH TELEMETRY_UNAVAILABLE", flush=True)


class ProgressReporter:
    def __init__(self, directory: Path, run_id: str, test_root: Path) -> None:
        self.directory, self.run_id, self.test_root = directory, run_id, Path(test_root)
        self.available = True
        self.cases: list[dict[str, Any]] = []
        self.active: dict[str, Any] | None = None
        self.current_raw: str | None = None
        self.occurrences: dict[str, int] = {}
        self.progress: dict[str, Any] = {
            "schema_version": "1.0.0", "run_id": run_id, "process": process_identity(os.getpid()),
            "total": None, "completed": 0, "collection_errors": 0, "current": None,
            "counts": {key: 0 for key in ("passed", "failed", "skipped", "xfailed", "xpassed", "xpassed_strict")},
            "session_finished": False, "exit_code": None, "telemetry_available": True,
            "last_event_at": utc_now(), "phase_started_at": utc_now(), "sequence": 0,
        }
        self.emit()

    def safe(self, operation: Any) -> None:
        if not self.available:
            return
        try:
            operation()
        except Exception:
            self.available = False
            self.progress["telemetry_available"] = False
            try:
                atomic_json(self.directory / "progress.json", self.progress)
            except Exception:
                pass
            print("THOTH TELEMETRY_UNAVAILABLE", flush=True)

    def emit(self) -> None:
        self.progress["sequence"] += 1
        self.progress["last_event_at"] = utc_now()
        atomic_json(self.directory / "progress.json", self.progress)

    def pytest_collection_finish(self, session: Any) -> None:
        def collected() -> None:
            self.progress["total"] = len(session.items)
            occurrences: dict[str, int] = {}
            items = []
            for item in session.items:
                occurrence = occurrences.get(item.nodeid, 0)
                occurrences[item.nodeid] = occurrence + 1
                identifier = hashlib.sha256(f"{item.nodeid}:{occurrence}".encode()).hexdigest()[:20]
                resolved = (self.test_root / item.location[0]).resolve()
                filename = resolved.relative_to(ROOT).as_posix() if resolved.is_relative_to(ROOT) else "external-test"
                function = item.location[2].split("[", 1)[0].split(".")[-1]
                items.append({"case_id": identifier, "file": filename, "function": function})
            atomic_json(self.directory / "collection.json", {
                "schema_version": "1.0.0", "run_id": self.run_id, "items": items,
            })
            self.emit()
        self.safe(collected)

    def pytest_collectreport(self, report: Any) -> None:
        if report.failed:
            self.safe(lambda: self.collection_error())

    def collection_error(self) -> None:
        self.progress["collection_errors"] += 1
        self.emit()

    def pytest_runtest_logstart(self, nodeid: str, location: tuple[str, int, str]) -> None:
        def started() -> None:
            occurrence = self.occurrences.get(nodeid, 0)
            self.occurrences[nodeid] = occurrence + 1
            case_id = hashlib.sha256(f"{nodeid}:{occurrence}".encode()).hexdigest()[:20]
            filename = Path(location[0]).as_posix()
            resolved = (self.test_root / filename).resolve()
            filename = resolved.relative_to(ROOT).as_posix() if resolved.is_relative_to(ROOT) else "external-test"
            if ".." in Path(filename).parts or Path(filename).is_absolute() or any(c in filename for c in "[]\n\r\x1b"):
                filename = "external-test"
            function = location[2].split("[", 1)[0].split(".")[-1]
            if not function.isidentifier():
                function = "test-case"
            self.active = {"case_id": case_id, "file": filename, "function": function,
                           "phases": {}, "outcome": "unfinished"}
            self.current_raw = nodeid
            self.phase("setup")
        self.safe(started)

    def phase(self, name: str) -> None:
        if self.active is not None:
            self.progress["current"] = {key: self.active[key] for key in ("case_id", "file", "function")}
            self.progress["current"]["phase"] = name
            self.progress["phase_started_at"] = utc_now()
            self.emit()

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_runtest_setup(self, item: Any):
        self.safe(lambda: self.phase("setup"))
        yield

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_runtest_call(self, item: Any):
        self.safe(lambda: self.phase("call"))
        yield

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_runtest_teardown(self, item: Any, nextitem: Any):
        self.safe(lambda: self.phase("teardown"))
        yield

    def pytest_runtest_logreport(self, report: Any) -> None:
        def reported() -> None:
            if self.active is None or report.nodeid != self.current_raw:
                raise ValueError("UNSUPPORTED_REPORT_ORDER")
            expected = getattr(report, "wasxfail", None) is not None
            strict = report.failed and isinstance(report.longrepr, str) and report.longrepr.startswith("[XPASS(strict)]")
            self.active["phases"][report.when] = {
                "outcome": report.outcome, "duration_s": report.duration,
                "expected_failure": expected, "strict_xpass": strict,
            }
        self.safe(reported)

    def pytest_runtest_logfinish(self, nodeid: str, location: tuple[str, int, str]) -> None:
        def finished() -> None:
            if self.active is None or nodeid != self.current_raw:
                raise ValueError("UNSUPPORTED_REPORT_ORDER")
            phases = self.active["phases"]
            if "teardown" not in phases:
                return
            outcome = "passed"
            if any(p["outcome"] == "failed" and not p["strict_xpass"] for p in phases.values()):
                outcome = "failed"
            elif any(p["strict_xpass"] for p in phases.values()):
                outcome = "xpassed_strict"
            elif any(p["outcome"] == "skipped" and p["expected_failure"] for p in phases.values()):
                outcome = "xfailed"
            elif any(p["outcome"] == "skipped" for p in phases.values()):
                outcome = "skipped"
            elif any(p["outcome"] == "passed" and p["expected_failure"] for p in phases.values()):
                outcome = "xpassed"
            self.active["outcome"] = outcome
            self.cases.append(self.active)
            self.progress["counts"][outcome] += 1
            self.progress["completed"] += 1
            self.progress["current"] = None
            self.progress["phase_started_at"] = None
            self.active, self.current_raw = None, None
            self.emit()
        self.safe(finished)

    def pytest_sessionfinish(self, session: Any, exitstatus: int) -> None:
        def finished() -> None:
            durations = [{"case_id": case["case_id"], "file": case["file"], "function": case["function"],
                          "phase": phase, "duration_s": report["duration_s"]}
                         for case in self.cases for phase, report in case["phases"].items()]
            top = sorted(durations, key=lambda item: item["duration_s"], reverse=True)[:20]
            atomic_json(self.directory / "durations.json", {"schema_version": "1.0.0", "run_id": self.run_id,
                        "cases": self.cases, "slowest_20": top, "unfinished": self.active})
            self.write_junit()
            self.progress.update(session_finished=True, exit_code=int(exitstatus))
            self.emit()
            run = read_json(self.directory / "run.json")
            if run["kind"] == "PYTEST_ONLY":
                run.update(state="VERIFY_FINISHED_NOT_SEALED" if int(exitstatus) == 0 else "INTERRUPTED" if int(exitstatus) == 2 and not self.progress["collection_errors"] else "TEST_FAILED",
                           verify_exit_code=int(exitstatus), finished_at=utc_now())
                atomic_json(self.directory / "run.json", run)
        self.safe(finished)

    def write_junit(self) -> None:
        root = ET.Element("testsuite", name="THOTH safe pytest observations", tests=str(len(self.cases)),
                          failures=str(self.progress["counts"]["failed"] + self.progress["counts"]["xpassed_strict"]),
                          skipped=str(self.progress["counts"]["skipped"] + self.progress["counts"]["xfailed"]))
        for case in self.cases:
            node = ET.SubElement(root, "testcase", name=case["function"] + "#" + case["case_id"],
                                 classname=case["file"], time=str(sum(p["duration_s"] for p in case["phases"].values())))
            if case["outcome"] in {"failed", "xpassed_strict"}:
                ET.SubElement(node, "failure", type=case["outcome"], message="Failure details omitted for privacy")
            elif case["outcome"] in {"skipped", "xfailed"}:
                ET.SubElement(node, "skipped", type=case["outcome"])
        temporary = self.directory / "junit-safe.xml.tmp"
        temporary.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))
        os.replace(temporary, self.directory / "junit-safe.xml")
