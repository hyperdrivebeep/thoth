"""Actual trusted CLI chain in a disposable source copy, not the user's live rules."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from scripts.verification_identity import reviewed_paths

ROOT = Path(__file__).resolve().parents[2]


def test_legacy_corruption_without_transaction_is_not_retroactively_authorized() -> None:
    # Keep the disposable root short on Windows, as the real candidate gate does.
    with tempfile.TemporaryDirectory(prefix="thoth-cli-") as temporary:
        _roundtrip(Path(temporary))


def _roundtrip(root: Path) -> None:
    for relative in reviewed_paths(ROOT):
        source = ROOT / relative
        if source.is_file():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    gate = root / ".thoth/architecture"
    env = {**os.environ, "THOTH_GATE_DIR": str(gate)}

    def run(script: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, f"scripts/{script}.py", *args], cwd=root, env=env,
            capture_output=True, encoding="utf-8", check=False, timeout=90,
        )

    prepared = run("prepare_architecture_preflight", "--acceptance", "A11", "--scope", "config")
    assert prepared.returncode == 0, prepared.stderr
    preflight = json.loads(prepared.stdout)
    identity = preflight["preflight_receipt_id"]
    archived = gate / "receipts" / f"{identity}.preflight.json"
    original_archive = archived.read_bytes()
    target = root / "config/architecture-conformance.json"
    original = target.read_bytes()
    target.write_bytes(original.replace(
        b'thoth.adapters.storage.bundle.SqliteStoreFactory"',
        b'thoth.adapters.storage.bundle.SqliteStoreFactory.open"',
    ))
    assert target.read_bytes() != original
    unrelated = root / "user-work.txt"
    unrelated.write_bytes(b"unrelated working change")
    failed = run("prepare_architecture_preflight", "--acceptance", "A11", "--scope", "config")
    assert failed.returncode != 0 and "SCHEMA_MIGRATION" in failed.stderr
    preview = run("restore_architecture_rules", "--preflight", identity)
    assert preview.returncode == 0, preview.stderr
    drift = json.loads(preview.stdout)["drift_id"]
    restored = run("restore_architecture_rules", "--preflight", identity, "--expected-drift", drift)
    assert restored.returncode != 0 and "diagnostic-only" in restored.stderr
    assert target.read_bytes() != original
    assert unrelated.read_bytes() == b"unrelated working change"
    assert archived.read_bytes() == original_archive
