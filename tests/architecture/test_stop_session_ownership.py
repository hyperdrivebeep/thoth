from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from scripts.architecture_gate_contract import (
    archive_receipt,
    calculate_preflight_receipt_id,
    preflight_archive_payload,
)

ROOT = Path(__file__).resolve().parents[2]


def owner_record(session: str = "session-a", turn: str = "turn-a") -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "1.0.0",
        "kind": "THOTH_HOST_PREFLIGHT_REQUEST",
        "session_id": session,
        "turn_id": turn,
        "tool_use_id": "call-fixture",
        "request_digest": "a" * 64,
        "observed_at": "2026-09-09T00:00:00+00:00",
        "previous_preflight_receipt_id": None,
        "nonce": "fixture-owner",
    }
    identity = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {**payload, "owner_receipt_id": identity}


def prepare_owned(gate: Path) -> dict[str, object]:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/prepare_architecture_preflight.py"),
            "--acceptance",
            "A11",
            "--scope",
            "src/thoth/__session_owner_probe.py",
        ],
        cwd=ROOT,
        env={**os.environ, "THOTH_GATE_DIR": str(gate)},
        capture_output=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    preflight = json.loads(result.stdout)
    proof = owner_record()
    archive_receipt(
        gate, receipt_id=str(proof["owner_receipt_id"]), kind="host-session", payload=proof
    )
    preflight["owner_context"] = proof
    preflight["preflight_receipt_id"] = calculate_preflight_receipt_id(preflight)
    archive_receipt(
        gate,
        receipt_id=preflight["preflight_receipt_id"],
        kind="preflight",
        payload=preflight_archive_payload(preflight),
    )
    (gate / "preflight.json").write_text(json.dumps(preflight), encoding="utf-8")
    return preflight


def stop(gate: Path, session: str, turn: str = "turn-b") -> dict[str, object]:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / ".codex/hooks/stop_acceptance_gate.py"),
        ],
        cwd=ROOT,
        env={**os.environ, "THOTH_PREFLIGHT_PATH": str(gate / "preflight.json")},
        input=json.dumps({"session_id": session, "turn_id": turn, "hook_event_name": "Stop"}),
        capture_output=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout) if result.stdout.strip() else {}


def test_other_session_stop_does_not_continue_or_change_owner_state(tmp_path: Path) -> None:
    prepare_owned(tmp_path)
    before = {
        str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()
    }
    assert stop(tmp_path, "session-b") == {}
    assert {
        str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()
    } == before


def test_owner_continuation_is_session_scoped_across_turns(tmp_path: Path) -> None:
    prepare_owned(tmp_path)
    result = stop(tmp_path, "session-a", "new-turn")
    assert result["decision"] == "block"
    assert "cancel with a historical receipt" not in str(result)


def test_owner_is_part_of_preflight_identity(tmp_path: Path) -> None:
    original = prepare_owned(tmp_path)
    forged = {**original, "owner_context": owner_record("session-b")}
    assert calculate_preflight_receipt_id(original) != calculate_preflight_receipt_id(forged)
