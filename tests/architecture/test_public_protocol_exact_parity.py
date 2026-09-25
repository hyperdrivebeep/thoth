from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from thoth.protocol.notifications import IMPLEMENTED_NOTIFICATIONS
from thoth.protocol.registry import PUBLIC_METHODS

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "schemas/protocol/public-method-catalog.json"


def test_public_method_manifest_exactly_matches_runtime_and_notifications() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    methods = manifest["methods"]
    by_name = {item["name"]: item for item in methods}
    assert len(methods) == len(by_name) == 338
    assert set(by_name) == set(PUBLIC_METHODS)
    assert sum(item["canonical"] for item in methods) == 332
    assert {
        f"project/source/scope/{name}" for name in ("read", "grant", "revoke", "assign", "update")
    } <= set(by_name)
    aliases = {item["name"]: item["alias_target"] for item in methods if not item["canonical"]}
    assert aliases == {
        "action/preflight/read": "action/authorization/prepare",
        "closure/finalize": "closure/decide",
        "export/prepare": "export/plan/create",
        "revision/compare": "revision/diff/read",
        "revision/history/read": "revision/list",
        "project/delete": "project/archive",
    }
    required = {
        "namespace",
        "surface",
        "canonical_owner",
        "policy",
        "normal_entrypoint",
        "behavioral_evidence",
        "alias_target",
    }
    assert all(required <= set(item) for item in methods)
    assert set(manifest["notifications"]) == set(IMPLEMENTED_NOTIFICATIONS)
    assert len(manifest["notifications"]) == 221
    assert all(item["namespace"] == item["name"].split("/", 1)[0] for item in methods)
    for namespace in ("model", "workspace"):
        entries = [item for item in methods if item["namespace"] == namespace]
        assert len(entries) == (4 if namespace == "model" else 3)
        for item in entries:
            assert item["canonical_owner"] == "THREAD_REQUEST_SETTINGS"
            assert item["normal_entrypoint"] == "thread/input"
            assert item["policy"] == "research-model-settings:versioned"
            assert item["behavioral_evidence"] == (
                ["U08", "U07"] if namespace == "model" else ["U08"]
            )


def test_public_protocol_checker_reports_zero_drift() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_public_protocol_parity.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["verdict"] == "PASS"
    assert payload["undeclared_runtime_methods"] == []
    assert payload["missing_runtime_methods"] == []
    assert payload["missing_documented_methods"] == []
