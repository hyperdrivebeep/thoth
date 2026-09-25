from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from scripts.verification_progress import atomic_json, run_directory, running_status

RUN = "vr-" + "1" * 32


def observation(root: Path) -> Path:
    directory = run_directory(root, RUN, create=True)
    pointer = root / ".thoth/architecture/preflight.json"
    pointer.parent.mkdir(parents=True)
    atomic_json(pointer, {"preflight_receipt_id": "a" * 64})
    atomic_json(
        directory / "run.json",
        {
            "schema_version": "1.0.0",
            "run_id": RUN,
            "kind": "FULL_VERIFICATION",
            "preflight_receipt_id": "a" * 64,
            "source_digest": "b" * 64,
            "index_digest": "c" * 64,
            "process": {
                "pid": 123,
                "creation_marker": "windows-filetime:100",
                "alive": True,
                "exit_code": None,
            },
            "state": "RUNNING",
            "verify_exit_code": None,
            "bundle_id": None,
        },
    )
    return directory


def alive_probe(pid: int) -> dict[str, Any]:
    return {
        "pid": pid,
        "creation_marker": "windows-filetime:100",
        "alive": True,
        "exit_code": None,
    }


def read(root: Path, **kwargs: Any) -> dict[str, Any]:
    probe = kwargs.pop("probe", alive_probe)
    return running_status(
        root,
        RUN,
        probe=probe,
        source_digest=kwargs.pop("source_digest", "b" * 64),
        index_digest_value="c" * 64,
        **kwargs,
    )


def test_fake_pass_metadata_never_creates_a_receipt(tmp_path: Path) -> None:
    directory = observation(tmp_path)
    value = json.loads((directory / "run.json").read_text())
    value.update(state="SEALED", verify_exit_code=0)
    atomic_json(directory / "run.json", value)
    result = read(tmp_path)
    assert result["status"] == "VERIFY_FINISHED_NOT_SEALED"
    assert result["sealed"] is False
    assert not (tmp_path / ".codex/verification/current.json").exists()


def test_successful_verification_shows_sealing_until_receipt_exists(tmp_path: Path) -> None:
    directory = observation(tmp_path)
    start = datetime(2026, 9, 9, tzinfo=UTC)
    value = json.loads((directory / "run.json").read_text())
    value.update(
        state="VERIFY_FINISHED_NOT_SEALED", verify_exit_code=0, finished_at=start.isoformat()
    )
    atomic_json(directory / "run.json", value)
    result = read(tmp_path, now=start + timedelta(seconds=5))
    assert result["stage"]["name"] == "SEAL"
    assert result["stage_elapsed_s"] == 5
    assert result["status"] == "VERIFY_FINISHED_NOT_SEALED" and result["sealed"] is False


def test_pid_reuse_and_unknown_inspection_are_distinct(tmp_path: Path) -> None:
    observation(tmp_path)

    def reused_probe(pid: int) -> dict[str, Any]:
        return {"pid": pid, "creation_marker": "windows-filetime:200", "alive": True}

    def unknown_probe(pid: int) -> dict[str, Any]:
        return {"pid": pid, "creation_marker": None, "alive": None}

    reused = read(tmp_path, probe=reused_probe)
    assert reused["process_state"] == "PID_REUSED" and reused["status"] == "EXIT_UNKNOWN"
    unknown = read(tmp_path, probe=unknown_probe)
    assert unknown["status"] == "PROCESS_STATE_UNKNOWN"


def test_historical_run_cannot_be_current_pass(tmp_path: Path) -> None:
    observation(tmp_path)
    result = read(tmp_path, source_digest="d" * 64)
    assert result["status"] == "HISTORICAL" and result["source_matches"] is False


def test_incompatible_old_proof_does_not_hide_live_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import verification_bundle_contract

    observation(tmp_path)

    def incompatible(_root: Path) -> None:
        raise ValueError("PROFILE_ACTUAL_DIFF_SELECTION_DIFFERS")

    monkeypatch.setattr(verification_bundle_contract, "current_index", incompatible)
    result = read(tmp_path)
    assert result["status"] == "RUNNING"
    assert result["prior_verification_state"] == "INVALID_OR_INCOMPATIBLE"
    assert result["sealed"] is False and result["full_suite_verified"] is False


def test_reading_one_hundred_times_is_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observation(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("a state query must not start a process")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    for _ in range(100):
        assert read(tmp_path)["status"] == "RUNNING"
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_broken_progress_is_unavailable_and_never_echoes_content(tmp_path: Path) -> None:
    directory = observation(tmp_path)
    (directory / "progress.json").write_text(
        '{"raw_capture":"PRIVATE_FIXTURE_TOKEN"', encoding="utf-8"
    )
    result = read(tmp_path)
    assert result["status"] == "TELEMETRY_UNAVAILABLE"
    assert "PRIVATE_FIXTURE_TOKEN" not in json.dumps(result)


def test_long_stage_is_diagnostic_not_failure(tmp_path: Path) -> None:
    directory = observation(tmp_path)
    start = datetime(2026, 9, 9, tzinfo=UTC)
    atomic_json(
        directory / "stages.json",
        {
            "schema_version": "1.0.0",
            "run_id": RUN,
            "producer_pid": 42,
            "last_event_at": start.isoformat(),
            "completed": [],
            "current": {
                "name": "BACKEND",
                "state": "STARTED",
                "started_at": start.isoformat(),
                "exit_code": None,
                "elapsed_s": 0,
            },
        },
    )
    result = read(tmp_path, now=start + timedelta(seconds=80))
    assert result["status"] == "RUNNING" and result["activity"] == "LONG_RUNNING_NEEDS_INSPECTION"
    assert result["stage_elapsed_s"] == 80
    assert result["eta"] == "UNKNOWN" and result["sealed"] is False
