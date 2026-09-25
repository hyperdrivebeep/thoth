from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from tests.architecture.owner_helpers import bind_owner

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.architecture_gate_contract import (  # noqa: E402
    archive_receipt,
    calculate_verification_receipt_id,
    scope_digest,
)
from scripts.verification_identity import index_digest, repository_digest  # noqa: E402


def _run(script: Path, *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        input=json.dumps({"session_id": "session-a", "turn_id": "turn-fixture"}),
        env={**os.environ, **env},
        check=False,
    )


def _verified_gate_without_wiki_sync(tmp_path: Path) -> tuple[Path, Path]:
    environment = {"THOTH_GATE_DIR": str(tmp_path)}
    prepared = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/prepare_architecture_preflight.py"),
            "--acceptance",
            "A11",
            "--scope",
            "src/thoth/domain/base.py",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, **environment},
        check=False,
    )
    assert prepared.returncode == 0, prepared.stderr
    preflight_path = tmp_path / "preflight.json"
    verification_path = tmp_path / "verification.json"
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    preflight = bind_owner(tmp_path)
    verified_at = "2026-09-03T00:00:00+00:00"
    draft: dict[str, object] = {
        "schema_version": "1.0.0",
        "preflight_receipt_id": preflight["preflight_receipt_id"],
        "acceptance_id": preflight["acceptance_id"],
        "rule_bundle_digest": preflight["rule_bundle_digest"],
        "scope_digest": scope_digest(list(preflight["declared_scope"])),
        "status": "PASS",
        "verified_at": verified_at,
    }
    verification_id = calculate_verification_receipt_id(draft)
    verification = {**draft, "verification_receipt_id": verification_id}
    preflight.update(
        {
            "status": "VERIFIED",
            "verified_at": verified_at,
            "verification_receipt_id": verification_id,
        }
    )
    preflight_path.write_text(json.dumps(preflight), encoding="utf-8")
    verification_path.write_text(json.dumps(verification), encoding="utf-8")
    archive_receipt(
        tmp_path,
        receipt_id=verification_id,
        kind="verification",
        payload=verification,
    )
    return preflight_path, verification_path


def _verified_gate_with_wiki_sync(tmp_path: Path) -> tuple[Path, Path]:
    environment = {"THOTH_GATE_DIR": str(tmp_path)}
    prepared = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/prepare_architecture_preflight.py"),
            "--acceptance",
            "A11",
            "--scope",
            "src/thoth/domain/base.py",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, **environment},
        check=False,
    )
    assert prepared.returncode == 0, prepared.stderr
    preflight_path = tmp_path / "preflight.json"
    verification_path = tmp_path / "verification.json"
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    recorded = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/record_wiki_sync.py"),
            "--no-change-reason",
            "test fixture changes no product truth",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, **environment},
        check=False,
    )
    assert recorded.returncode == 0, recorded.stderr
    wiki_sync = json.loads(recorded.stdout)
    verified_at = "2026-09-03T00:00:00+00:00"
    draft: dict[str, object] = {
        "schema_version": "1.0.0",
        "preflight_receipt_id": preflight["preflight_receipt_id"],
        "acceptance_id": preflight["acceptance_id"],
        "rule_bundle_digest": preflight["rule_bundle_digest"],
        "scope_digest": scope_digest(list(preflight["declared_scope"])),
        "wiki_sync_receipt_id": wiki_sync["wiki_sync_receipt_id"],
        "repository_digest": repository_digest(ROOT),
        "index_digest": index_digest(ROOT),
        "status": "PASS",
        "verified_at": verified_at,
    }
    verification_id = calculate_verification_receipt_id(draft)
    verification = {**draft, "verification_receipt_id": verification_id}
    preflight.update(
        {
            "status": "VERIFIED",
            "verified_at": verified_at,
            "verification_receipt_id": verification_id,
        }
    )
    preflight_path.write_text(json.dumps(preflight), encoding="utf-8")
    verification_path.write_text(json.dumps(verification), encoding="utf-8")
    archive_receipt(
        tmp_path,
        receipt_id=verification_id,
        kind="verification",
        payload=verification,
    )
    return preflight_path, verification_path


def test_stop_hook_rejects_verified_gate_without_wiki_sync(tmp_path: Path) -> None:
    preflight, verification = _verified_gate_without_wiki_sync(tmp_path)
    result = _run(
        ROOT / ".codex/hooks/stop_acceptance_gate.py",
        env={
            "THOTH_PREFLIGHT_PATH": str(preflight),
            "THOTH_VERIFICATION_PATH": str(verification),
        },
    )
    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert output["continue"] is False
    assert "wiki" in output["stopReason"].lower()


def test_legacy_wiki_receipt_does_not_establish_owned_completion(tmp_path: Path) -> None:
    preflight, verification = _verified_gate_with_wiki_sync(tmp_path)
    result = _run(
        ROOT / ".codex/hooks/stop_acceptance_gate.py",
        env={
            "THOTH_PREFLIGHT_PATH": str(preflight),
            "THOTH_VERIFICATION_PATH": str(verification),
        },
    )
    assert result.returncode == 0
    assert "LEGACY_OWNER_UNBOUND" in result.stdout
