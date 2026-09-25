"""Explicitly simulated profile receipts for protocol tests, never repository completion proof."""

from __future__ import annotations

import copy
import platform
import shutil
import sys
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

from scripts.architecture_contract import load_manifest
from scripts.architecture_gate_contract import calculate_verification_receipt_id
from scripts.verification_bundle_contract import build_bundle, current_index, publish_bundle
from scripts.verification_identity import capture_source_manifest
from scripts.verification_profiles import CONTROL_PATHS, canonical_digest, select_manifest_profile
from tests.architecture.test_portable_verification_bundle import fixture_bundle

RUNTIME = {
    "python_version": platform.python_version(),
    "platform": sys.platform,
    "shell": Path(shutil.which("pwsh.exe") or shutil.which("powershell.exe") or "fixture").name,
    "pytest_version": version("pytest"),
    "python_implementation": platform.python_implementation(),
    "pytest_autoload": "enabled",
}


def observation_proof(selection: dict[str, Any]) -> dict[str, Any]:
    return {
        "stages": [
            {
                "name": name,
                "state": "FINISHED",
                "started_at": datetime.now(UTC).isoformat(),
                "exit_code": 0,
                "elapsed_s": 0,
            }
            for name in selection["expected_stages"]
        ],
        "collection": [
            {
                "case_id": "f" * 20,
                "file": "tests/architecture/test_fixture.py",
                "function": "test_fixture",
            }
        ],
        "results": [{"case_id": "f" * 20, "outcome": "passed"}],
        "pytest_exit_code": 0,
    }


def profile_bundle(
    root: Path,
    seed: dict[str, Any],
    baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = capture_source_manifest(root)
    selection = select_manifest_profile(
        source,
        baseline=None if baseline is None else baseline["source_manifest"],
        baseline_id=None if baseline is None else baseline["bundle_id"],
        baseline_policy=None
        if baseline is None
        else baseline["run_evidence"]["selection"]["policy"],
        rule_documents=tuple(map(str, load_manifest()["rule_documents"])),
        runtime=RUNTIME,
        baseline_runtime=None if baseline is None else baseline["run_evidence"]["runtime"],
    )
    verification = copy.deepcopy(seed["verification"])
    stamp = datetime.now(UTC).isoformat()
    verification.update(
        repository_digest=source["repository_digest"],
        index_digest=source["index_digest"],
        source_manifest_digest=source["manifest_digest"],
        source_manifest_policy=source["policy"],
        verification_profile=selection["profile"],
        full_suite_verified=selection["requires_full_suite"],
        selection_digest=canonical_digest(selection),
        verified_at=stamp,
    )
    verification["verification_receipt_id"] = calculate_verification_receipt_id(verification)
    return build_bundle(
        preflight=seed["preflight"],
        wiki=seed["wiki_sync"],
        verification=verification,
        source_manifest=source,
        run_evidence={
            "kind": selection["profile"] + "_VERIFY_SUBPROCESS",
            "command": ["Makefile.ps1", "verify" if selection["profile"] == "FULL" else "tooling"],
            "exit_code": 0,
            "started_at": stamp,
            "finished_at": stamp,
            "run_id": "vr-" + ("1" if selection["profile"] == "FULL" else "2") * 32,
            "runtime": RUNTIME,
            "selection": selection,
            "observations": observation_proof(selection),
        },
    )


def publish(root: Path, bundle: dict[str, Any]) -> None:
    index = current_index(root)
    publish_bundle(root, bundle, expected_index_id=None if index is None else index["index_id"])


def baseline_fixture(root: Path, seed: dict[str, Any] | None = None) -> dict[str, Any]:
    seed = fixture_bundle(root, owned=True) if seed is None else seed
    for path in sorted(CONTROL_PATHS):
        target = root / path
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("# Simulated policy source for protocol tests\n", encoding="utf-8")
    bundle = profile_bundle(root, seed)
    assert bundle["verification"]["verification_profile"] == "FULL"
    publish(root, bundle)
    return bundle


def changed_tool(root: Path) -> None:
    (root / "scripts/pytest_progress.py").write_text(
        "# Changed reporting-only fixture\n", encoding="utf-8"
    )
