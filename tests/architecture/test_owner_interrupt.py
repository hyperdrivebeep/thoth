import json
import os
import subprocess
import sys
from pathlib import Path

from tests.architecture.test_stop_session_ownership import ROOT, prepare_owned, stop


def test_host_interrupt_does_not_restart_or_release_owner(tmp_path: Path) -> None:
    prepare_owned(tmp_path)
    before = {
        str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()
    }
    response = subprocess.run(
        [sys.executable, str(ROOT / ".codex/hooks/stop_acceptance_gate.py")],
        cwd=ROOT,
        input=json.dumps(
            {
                "hook_event_name": "Interrupt",
                "session_id": "session-a",
                "turn_id": "turn-interrupted",
            }
        ),
        env={**os.environ, "THOTH_PREFLIGHT_PATH": str(tmp_path / "preflight.json")},
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert response.returncode == 0 and response.stdout == ""
    assert {
        str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()
    } == before
    assert stop(tmp_path, "session-a", "turn-resumed")["decision"] == "block"
