"""Simulated observations for completion-state unit tests; never live verification evidence."""

import os
from pathlib import Path

from scripts.verification_profiles import FULL_STAGES, TOOLING_STAGES
from scripts.verification_progress import (
    OUTCOMES,
    atomic_json,
    process_identity,
    run_directory,
    utc_now,
)


def simulated_reports(root: Path, run_id: str, *, profile: str = "FULL") -> None:
    directory = run_directory(root, run_id)
    atomic_json(
        directory / "progress.json",
        {
            "schema_version": "1.0.0",
            "run_id": run_id,
            "process": process_identity(os.getpid()),
            "total": 1,
            "completed": 1,
            "collection_errors": 0,
            "current": None,
            "counts": {key: int(key == "passed") for key in OUTCOMES},
            "session_finished": True,
            "exit_code": 0,
            "telemetry_available": True,
            "last_event_at": utc_now(),
            "phase_started_at": None,
            "sequence": 1,
        },
    )
    atomic_json(
        directory / "durations.json",
        {"run_id": run_id, "cases": [{"case_id": "f" * 20, "outcome": "passed"}], "slowest_20": []},
    )
    atomic_json(
        directory / "collection.json",
        {
            "schema_version": "1.0.0",
            "run_id": run_id,
            "items": [
                {
                    "case_id": "f" * 20,
                    "file": "tests/architecture/test_fixture.py",
                    "function": "test_fixture",
                }
            ],
        },
    )
    atomic_json(
        directory / "stages.json",
        {
            "schema_version": "1.0.0",
            "run_id": run_id,
            "current": None,
            "completed": [
                {
                    "name": name,
                    "state": "FINISHED",
                    "started_at": utc_now(),
                    "exit_code": 0,
                    "elapsed_s": 0,
                }
                for name in (FULL_STAGES if profile == "FULL" else TOOLING_STAGES)
            ],
            "last_event_at": utc_now(),
            "producer_pid": 1,
        },
    )
    (directory / "junit-safe.xml").write_text(
        '<testsuite tests="1"><testcase name="fixture"/></testsuite>', encoding="utf-8"
    )
