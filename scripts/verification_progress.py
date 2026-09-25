"""Non-authoritative local verification observations. Reading never starts or stops work."""

from __future__ import annotations

import ctypes
import json
import math
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
RUN_PATTERN = re.compile(r"vr-[0-9a-f]{32}")
MAX_JSON_BYTES = 8 * 1024 * 1024
OUTCOMES = {"passed", "failed", "skipped", "xfailed", "xpassed", "xpassed_strict"}
STAGES = {"ARCHITECTURE", "LINT", "TYPE", "BACKEND", "TOOLING", "WEB_LINT", "WEB_TYPE", "WEB_TEST", "WEB_BUILD", "DOCTOR", "SEAL"}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def run_directory(root: Path, run_id: str, *, create: bool = False) -> Path:
    if not isinstance(run_id, str) or RUN_PATTERN.fullmatch(run_id) is None:
        raise ValueError("INVALID_VERIFICATION_RUN_ID")
    root = root.resolve()
    target = root / ".thoth" / "verification-runs" / run_id
    if not target.resolve().is_relative_to(root):
        raise ValueError("VERIFICATION_RUN_PATH_ESCAPES_ROOT")
    for path in (root / ".thoth", target.parent, target):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError("VERIFICATION_RUN_REPARSE_PATH")
    if create:
        target.mkdir(parents=True, exist_ok=True)
    return target


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    encoded = (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode()
    if len(encoded) > MAX_JSON_BYTES:
        raise ValueError("TELEMETRY_SIZE_LIMIT")
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
        for attempt in range(3):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 2:
                    raise
                time.sleep(0.01)  # Retry only atomic file replacement, never a test or verification.
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise ValueError("TELEMETRY_REPARSE_FILE")
    with path.open("rb") as handle:
        raw = handle.read(MAX_JSON_BYTES + 1)
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError("TELEMETRY_SIZE_LIMIT")
    value = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("TELEMETRY_OBJECT_REQUIRED")
    return value


def process_identity(pid: int) -> dict[str, Any]:
    unknown = {"pid": pid, "creation_marker": None, "alive": None, "exit_code": None}
    if type(pid) is not int or pid <= 0:
        return unknown
    if os.name == "nt":
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
        kernel.GetProcessTimes.restype = wintypes.BOOL
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x1000 | 0x100000, False, pid)  # Query and synchronize only.
        if not handle:
            return {**unknown, "alive": False if ctypes.get_last_error() == 87 else None}
        try:
            created, exited, cpu_kernel, cpu_user = (wintypes.FILETIME() for _ in range(4))
            if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                          ctypes.byref(cpu_kernel), ctypes.byref(cpu_user)):
                return unknown
            marker = (created.dwHighDateTime << 32) | created.dwLowDateTime
            waiting = kernel.WaitForSingleObject(handle, 0)
            alive = True if waiting == 258 else False if waiting == 0 else None
            exit_code = wintypes.DWORD()
            observed_exit = int(exit_code.value) if alive is False and kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)) else None
            return {"pid": pid, "creation_marker": f"windows-filetime:{marker}", "alive": alive, "exit_code": observed_exit}
        finally:
            kernel.CloseHandle(handle)
    if sys.platform.startswith("linux"):
        try:
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            return {"pid": pid, "creation_marker": f"linux:{boot}:{fields[19]}", "alive": fields[0] != "Z", "exit_code": None}
        except FileNotFoundError:
            return {**unknown, "alive": False}
        except (OSError, IndexError):
            return unknown
    return unknown


def start_run(root: Path, *, kind: str, preflight: dict[str, Any] | None = None,
              source: dict[str, Any] | None = None, run_id: str | None = None) -> tuple[Path, dict[str, Any]]:
    from scripts.verification_identity import capture_source_manifest

    identifier = run_id or "vr-" + uuid4().hex
    directory = run_directory(root, identifier, create=True)
    if (directory / "run.json").exists():
        raise ValueError("VERIFICATION_RUN_ALREADY_EXISTS")
    manifest = source if source is not None else capture_source_manifest(root)
    if preflight is None:
        pointer = root / ".thoth/architecture/preflight.json"
        preflight = read_json(pointer) if pointer.exists() else {}
    value = {
        "schema_version": "1.0.0", "run_id": identifier, "kind": kind,
        "preflight_receipt_id": preflight.get("preflight_receipt_id"),
        "source_digest": manifest["repository_digest"], "index_digest": manifest["index_digest"],
        "rule_bundle_digest": preflight.get("rule_bundle_digest"),
        "process": process_identity(os.getpid()), "started_at": utc_now(), "finished_at": None,
        "state": "RUNNING", "verify_exit_code": None, "bundle_id": None,
        "verification_receipt_id": None, "semantic_truth_certified": False,
    }
    atomic_json(directory / "run.json", value)
    return directory, value


def update_run(directory: Path, value: dict[str, Any], **changes: Any) -> None:
    value.update(changes)
    atomic_json(directory / "run.json", value)


def require_observations(
    directory: Path, run_id: str, *, selection: dict[str, Any] | None = None,
    source: dict[str, Any] | None = None, baseline: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    progress = validated_progress(read_json(directory / "progress.json"), run_id)
    if (progress.get("run_id") != run_id or progress.get("session_finished") is not True
            or progress.get("telemetry_available") is not True or progress.get("exit_code") != 0
            or progress.get("completed") != progress.get("total")):
        raise ValueError("TELEMETRY_UNAVAILABLE_OR_INCOMPLETE")
    if progress["counts"]["failed"] or progress["counts"]["xpassed_strict"] or progress["collection_errors"]:
        raise ValueError("TELEMETRY_CONTRADICTS_SUCCESSFUL_EXIT")
    for name in ("durations.json", "stages.json"):
        if read_json(directory / name).get("run_id") != run_id:
            raise ValueError("TELEMETRY_RUN_MISMATCH")
    stages = validated_stages(read_json(directory / "stages.json"), run_id)
    expected = ["ARCHITECTURE", "LINT", "TYPE", "BACKEND", "WEB_LINT", "WEB_TYPE", "WEB_TEST", "WEB_BUILD", "DOCTOR"]
    if selection is not None:
        expected = selection["expected_stages"]
    if stages["current"] is not None or [s["name"] for s in stages["completed"]] != expected or any(s["state"] != "FINISHED" or s["exit_code"] != 0 for s in stages["completed"]):
        raise ValueError("TELEMETRY_STAGES_INCOMPLETE")
    durations = read_json(directory / "durations.json")
    if not isinstance(durations.get("cases"), list) or len(durations["cases"]) != progress["completed"]:
        raise ValueError("TELEMETRY_DURATIONS_INCOMPLETE")
    with (directory / "junit-safe.xml").open("rb") as handle:
        xml = handle.read(MAX_JSON_BYTES + 1)
    if len(xml) > MAX_JSON_BYTES or b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
        raise ValueError("TELEMETRY_JUNIT_INVALID")
    report = ET.fromstring(xml)
    if report.tag != "testsuite" or len(report.findall("testcase")) != progress["completed"]:
        raise ValueError("TELEMETRY_JUNIT_INCOMPLETE")
    if selection is not None:
        from scripts.verification_profile_contract import validate_observation_proof

        collected = read_json(directory / "collection.json")
        if collected.get("run_id") != run_id or source is None:
            raise ValueError("PROFILE_COLLECTION_BINDING_DIFFERS")
        observations = {
            "stages": stages["completed"], "collection": collected["items"],
            "results": [{"case_id": c["case_id"], "outcome": c["outcome"]} for c in durations["cases"]],
            "pytest_exit_code": progress["exit_code"],
        }
        validate_observation_proof(observations, selection, source, baseline)
        return observations
    return None


def _elapsed(value: object, now: datetime) -> float | None:
    if not isinstance(value, str):
        return None
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        return None
    return round(max(0.0, (now - stamp).total_seconds()), 3)


def validated_process(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"pid", "creation_marker", "alive", "exit_code"}:
        raise ValueError("TELEMETRY_PROCESS_INVALID")
    if type(value["pid"]) is not int or value["pid"] <= 0:
        raise ValueError("TELEMETRY_PROCESS_INVALID")
    marker = value["creation_marker"]
    if marker is not None and (not isinstance(marker, str) or len(marker) > 160 or re.fullmatch(r"[A-Za-z0-9:-]+", marker) is None):
        raise ValueError("TELEMETRY_PROCESS_INVALID")
    if value["alive"] is not None and type(value["alive"]) is not bool:
        raise ValueError("TELEMETRY_PROCESS_INVALID")
    if value["exit_code"] is not None and type(value["exit_code"]) is not int:
        raise ValueError("TELEMETRY_PROCESS_INVALID")
    return value


def validated_progress(value: dict[str, Any], run_id: str) -> dict[str, Any]:
    expected = {"schema_version", "run_id", "process", "total", "completed", "collection_errors",
                "current", "counts", "session_finished", "exit_code", "telemetry_available",
                "last_event_at", "phase_started_at", "sequence"}
    if set(value) != expected or value["schema_version"] != "1.0.0" or value["run_id"] != run_id:
        raise ValueError("TELEMETRY_PROGRESS_INVALID")
    validated_process(value["process"])
    for key in ("completed", "collection_errors", "sequence"):
        if type(value[key]) is not int or not 0 <= value[key] <= 10_000_000:
            raise ValueError("TELEMETRY_COUNT_INVALID")
    total = value["total"]
    if total is not None and (type(total) is not int or not value["completed"] <= total <= 10_000_000):
        raise ValueError("TELEMETRY_COUNT_INVALID")
    counts = value["counts"]
    if not isinstance(counts, dict) or set(counts) != OUTCOMES or any(type(n) is not int or n < 0 for n in counts.values()) or sum(counts.values()) != value["completed"]:
        raise ValueError("TELEMETRY_COUNT_INVALID")
    if type(value["session_finished"]) is not bool or type(value["telemetry_available"]) is not bool:
        raise ValueError("TELEMETRY_STATE_INVALID")
    if value["exit_code"] is not None and type(value["exit_code"]) is not int:
        raise ValueError("TELEMETRY_STATE_INVALID")
    current = value["current"]
    if current is not None:
        if not isinstance(current, dict) or set(current) != {"case_id", "file", "function", "phase"}:
            raise ValueError("TELEMETRY_CASE_INVALID")
        if not isinstance(current["case_id"], str) or re.fullmatch(r"[0-9a-f]{20}", current["case_id"]) is None:
            raise ValueError("TELEMETRY_CASE_INVALID")
        if not isinstance(current["function"], str) or (not current["function"].isidentifier() and current["function"] != "test-case") or len(current["function"]) > 200:
            raise ValueError("TELEMETRY_CASE_INVALID")
        filename = current["file"]
        if not isinstance(filename, str) or len(filename) > 500 or Path(filename).is_absolute() or ".." in Path(filename).parts or any(c in filename for c in "[]\n\r\x1b"):
            raise ValueError("TELEMETRY_CASE_INVALID")
        if current["phase"] not in {"setup", "call", "teardown"}:
            raise ValueError("TELEMETRY_CASE_INVALID")
    _elapsed(value["last_event_at"], datetime.now(UTC))
    _elapsed(value["phase_started_at"], datetime.now(UTC))
    return value


def validated_stages(value: dict[str, Any], run_id: str) -> dict[str, Any]:
    if set(value) != {"schema_version", "run_id", "current", "completed", "last_event_at", "producer_pid"} or value["run_id"] != run_id or value["schema_version"] != "1.0.0":
        raise ValueError("TELEMETRY_STAGE_INVALID")
    if not isinstance(value["completed"], list) or len(value["completed"]) > 32:
        raise ValueError("TELEMETRY_STAGE_INVALID")
    for entry in [*value["completed"], *([] if value["current"] is None else [value["current"]])]:
        if not isinstance(entry, dict) or set(entry) != {"name", "state", "started_at", "exit_code", "elapsed_s"}:
            raise ValueError("TELEMETRY_STAGE_INVALID")
        if entry["name"] not in STAGES or entry["state"] not in {"STARTED", "FINISHED", "FAILED", "EXIT_UNKNOWN"}:
            raise ValueError("TELEMETRY_STAGE_INVALID")
        if entry["exit_code"] is not None and type(entry["exit_code"]) is not int:
            raise ValueError("TELEMETRY_STAGE_INVALID")
        if not isinstance(entry["elapsed_s"], (float, int)) or not math.isfinite(entry["elapsed_s"]) or entry["elapsed_s"] < 0:
            raise ValueError("TELEMETRY_STAGE_INVALID")
        _elapsed(entry["started_at"], datetime.now(UTC))
    return value


def running_status(root: Path, run_id: str, *, now: datetime | None = None,
                   probe: Any = process_identity, source_digest: str | None = None,
                   index_digest_value: str | None = None, long_after: float = 60.0) -> dict[str, Any]:
    from scripts.verification_identity import index_digest, repository_digest
    from scripts.verification_bundle_contract import current_index, read_bundle

    current_time = now or datetime.now(UTC)
    base: dict[str, Any] = {"run_id": run_id, "status": "TELEMETRY_UNAVAILABLE", "sealed": False}
    try:
        directory = run_directory(root, run_id)
        run = read_json(directory / "run.json")
        if run.get("schema_version") != "1.0.0" or run.get("run_id") != run_id:
            raise ValueError("TELEMETRY_RUN_MISMATCH")
        if run.get("kind") not in {"FULL_VERIFICATION", "TOOLING_VERIFICATION", "PYTEST_ONLY"} or run.get("state") not in {"RUNNING", "SEALED", "TOOLING_SEALED", "VERIFY_FINISHED_NOT_SEALED", "VERIFY_FAILED", "TEST_FAILED", "INTERRUPTED", "PROCESS_START_FAILED"}:
            raise ValueError("TELEMETRY_RUN_INVALID")
        if run.get("verify_exit_code") is not None and type(run["verify_exit_code"]) is not int:
            raise ValueError("TELEMETRY_RUN_INVALID")
        recorded_process = validated_process(run["process"])
        for key in ("source_digest", "index_digest"):
            if not isinstance(run.get(key), str) or re.fullmatch(r"[0-9a-f]{64}", run[key]) is None:
                raise ValueError("TELEMETRY_SOURCE_INVALID")
        if run.get("preflight_receipt_id") is not None and (not isinstance(run["preflight_receipt_id"], str) or re.fullmatch(r"[0-9a-f]{64}", run["preflight_receipt_id"]) is None):
            raise ValueError("TELEMETRY_PREFLIGHT_INVALID")
        observed = probe(recorded_process["pid"])
        identity_matches = (recorded_process.get("creation_marker") is not None
                            and recorded_process.get("creation_marker") == observed.get("creation_marker"))
        process_state = "ALIVE" if identity_matches and observed.get("alive") is True else (
            "EXITED" if observed.get("alive") is False else
            "PID_REUSED" if observed.get("creation_marker") and not identity_matches else "UNKNOWN")
        actual_source = source_digest if source_digest is not None else repository_digest(root)
        actual_index = index_digest_value if index_digest_value is not None else index_digest(root)
        pointer = root / ".thoth/architecture/preflight.json"
        active_preflight = read_json(pointer).get("preflight_receipt_id") if pointer.exists() else None
        historical = (run.get("source_digest") != actual_source or run.get("index_digest") != actual_index
                      or run.get("preflight_receipt_id") != active_preflight)
        base.update(process_state=process_state, process={"pid": recorded_process["pid"], "creation_marker": recorded_process["creation_marker"]}, historical=historical,
                    kind=run["kind"],
                    verification_profile="TOOLING" if run["kind"] == "TOOLING_VERIFICATION" else "FULL" if run["kind"] == "FULL_VERIFICATION" else None,
                    full_suite_verified=False,
                    source_matches=run.get("source_digest") == actual_source,
                    preflight_receipt_id=run.get("preflight_receipt_id"),
                    verify_exit_code=run.get("verify_exit_code"), eta="UNKNOWN")
        progress = validated_progress(read_json(directory / "progress.json"), run_id) if (directory / "progress.json").exists() else None
        stages = validated_stages(read_json(directory / "stages.json"), run_id) if (directory / "stages.json").exists() else None
        if progress is not None:
            if progress.get("run_id") != run_id or progress.get("schema_version") != "1.0.0":
                raise ValueError("TELEMETRY_PROGRESS_INVALID")
            base["pytest"] = {key: progress.get(key) for key in (
                "current", "total", "completed", "counts", "collection_errors", "session_finished",
                "exit_code", "last_event_at", "phase_started_at", "telemetry_available")}
            base["test_elapsed_s"] = _elapsed(progress.get("phase_started_at"), current_time)
        if stages is not None:
            if stages.get("run_id") != run_id:
                raise ValueError("TELEMETRY_STAGE_MISMATCH")
            base["stage"] = stages.get("current")
            base["stages"] = stages.get("completed", [])
            if stages.get("current") is not None:
                base["stage_elapsed_s"] = _elapsed(stages["current"]["started_at"], current_time)
        if run.get("verify_exit_code") == 0 and process_state == "ALIVE" and run.get("state") == "VERIFY_FINISHED_NOT_SEALED":
            base["stage"] = {"name": "SEAL", "state": "STARTED", "started_at": run.get("finished_at"), "exit_code": None}
            base["stage_elapsed_s"] = _elapsed(run.get("finished_at"), current_time)
        status = "RUNNING"
        if run.get("verify_exit_code") == 0:
            status = "VERIFY_FINISHED_NOT_SEALED"
        elif run.get("state") in {"TEST_FAILED", "INTERRUPTED", "PROCESS_START_FAILED", "VERIFY_FAILED", "VERIFY_FINISHED_NOT_SEALED"}:
            status = run["state"]
        elif process_state in {"EXITED", "PID_REUSED"}:
            status = "EXIT_UNKNOWN"
        elif process_state == "UNKNOWN":
            status = "PROCESS_STATE_UNKNOWN"
        if progress is not None and progress.get("session_finished") and progress.get("exit_code"):
            status = "COLLECTION_FAILED" if progress["collection_errors"] else "INTERRUPTED" if progress["exit_code"] == 2 else "TEST_FAILED" if progress["exit_code"] == 1 else "VERIFY_FAILED"
        try:
            proof = current_index(root)
        except (OSError, ValueError, TypeError, KeyError):
            # Historical proof incompatibility must not hide independently valid
            # live observations. It never authorizes sealing or a baseline claim.
            proof = None
            base["prior_verification_state"] = "INVALID_OR_INCOMPATIBLE"
        if proof is not None and proof["bundle_id"] == run.get("bundle_id"):
            bundle = read_bundle(root, proof["bundle_id"])
            if (bundle["run_evidence"].get("run_id") == run_id
                    and bundle["verification"]["repository_digest"] == run.get("source_digest")
                    and bundle["preflight"]["preflight_receipt_id"] == run.get("preflight_receipt_id")):
                from scripts.verification_profile_contract import receipt_profile

                full = receipt_profile(bundle["verification"]) == "FULL"
                status = "SEALED" if full else "TOOLING_VERIFIED"
                base.update(sealed=full, tooling_sealed=not full, full_suite_verified=full,
                            verification_receipt_id=proof["verification_receipt_id"])
        if progress is not None and progress.get("telemetry_available") is not True:
            status = "TELEMETRY_UNAVAILABLE"
        if progress is None and (run["kind"] == "PYTEST_ONLY" or (directory / "backend-owner.json").exists() or any(s["name"] in {"BACKEND", "TOOLING"} for s in (stages or {}).get("completed", []))):
            status = "TELEMETRY_UNAVAILABLE"
        last_event = (stages or {}).get("last_event_at")
        if progress is not None and not progress.get("session_finished"):
            last_event = progress.get("last_event_at")
        age = _elapsed(last_event, current_time)
        base["last_event_age_s"] = age
        base["activity"] = "LONG_RUNNING_NEEDS_INSPECTION" if status == "RUNNING" and age is not None and age >= long_after else "OBSERVED"
        base["recorded_status"] = status
        base["status"] = "HISTORICAL" if historical else status
        base["terminal"] = historical or status in {"SEALED", "TOOLING_VERIFIED", "TEST_FAILED", "COLLECTION_FAILED", "INTERRUPTED", "PROCESS_START_FAILED", "VERIFY_FAILED", "EXIT_UNKNOWN"} or (status == "VERIFY_FINISHED_NOT_SEALED" and process_state != "ALIVE")
        return base
    except (OSError, ValueError, TypeError, KeyError):
        return {"run_id": run_id, "status": "TELEMETRY_UNAVAILABLE", "sealed": False, "terminal": False}
