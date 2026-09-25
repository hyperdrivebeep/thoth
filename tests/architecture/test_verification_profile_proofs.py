from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from scripts.architecture_gate_contract import calculate_verification_receipt_id
from scripts.verification_bundle_contract import (
    digest,
    read_bundle,
    require_portable_receipt,
)
from scripts.verification_identity import capture_source_manifest
from scripts.verification_profile_contract import select_current, validate_profile_baseline
from scripts.verification_progress import running_status, start_run, update_run
from scripts.verification_status import verification_status
from tests.architecture.profile_helpers import (
    baseline_fixture,
    changed_tool,
    profile_bundle,
    publish,
)


def test_partial_proof_is_never_a_full_pass_and_baseline_stays_full(tmp_path: Path) -> None:
    baseline = baseline_fixture(tmp_path)
    changed_tool(tmp_path)
    partial = profile_bundle(tmp_path, baseline, baseline)
    publish(tmp_path, partial)
    status = verification_status(tmp_path)
    assert status["verification_profile"] == "TOOLING"
    assert status["current_scope_verified"] is True
    assert status["current_source_verified"] is False
    assert status["full_suite_verified"] is False
    with pytest.raises(ValueError, match="FULL_SUITE_REQUIRED"):
        require_portable_receipt(tmp_path, partial["verification"])
    require_portable_receipt(tmp_path, partial["verification"], allow_tooling=True)
    selection, original = select_current(
        tmp_path,
        capture_source_manifest(tmp_path),
        tuple(partial["run_evidence"]["selection"]["rule_documents"]),
    )
    assert original is not None and original["bundle_id"] == baseline["bundle_id"]
    assert selection["profile"] == "TOOLING"

    run_id = partial["run_evidence"]["run_id"]
    directory, run = start_run(
        tmp_path,
        kind="TOOLING_VERIFICATION",
        preflight=partial["preflight"],
        run_id=run_id,
    )
    update_run(
        directory, run, state="TOOLING_SEALED", verify_exit_code=0, bundle_id=partial["bundle_id"]
    )
    pointer = tmp_path / ".thoth/architecture/preflight.json"
    pointer.write_text(json.dumps(partial["preflight"]), encoding="utf-8")
    live = running_status(tmp_path, run_id)
    assert live["status"] == "TOOLING_VERIFIED"
    assert live["sealed"] is False and live["full_suite_verified"] is False
    assert live["tooling_sealed"] is True and live["terminal"] is True


@pytest.mark.parametrize(
    "mutation", ["empty", "missing_stage", "missing_result", "missing_file", "skipped"]
)
def test_partial_mandatory_evidence_cannot_be_removed(tmp_path: Path, mutation: str) -> None:
    baseline = baseline_fixture(tmp_path)
    changed_tool(tmp_path)
    partial = profile_bundle(tmp_path, baseline, baseline)
    evidence = partial["run_evidence"]["observations"]
    if mutation == "empty":
        evidence["collection"] = []
        evidence["results"] = []
    elif mutation == "missing_stage":
        evidence["stages"].pop(0)
    elif mutation == "missing_file":
        evidence["collection"][0]["file"] = "tests/architecture/test_different.py"
    elif mutation == "skipped":
        evidence["results"][0]["outcome"] = "skipped"
    else:
        evidence["results"] = []
    with pytest.raises(ValueError, match="PROFILE_"):
        validate_profile_baseline(tmp_path, partial)


def test_removed_mandatory_baseline_case_is_not_hidden_by_current_collection(
    tmp_path: Path,
) -> None:
    baseline = baseline_fixture(tmp_path)
    modified = copy.deepcopy(baseline)
    modified["run_evidence"]["observations"]["collection"].append(
        {
            "case_id": "e" * 20,
            "file": "tests/architecture/test_fixture.py",
            "function": "test_second",
        }
    )
    modified["run_evidence"]["observations"]["results"].append(
        {
            "case_id": "e" * 20,
            "outcome": "passed",
        }
    )
    modified["bundle_id"] = digest({k: v for k, v in modified.items() if k != "bundle_id"})
    publish(tmp_path, modified)
    changed_tool(tmp_path)
    partial = profile_bundle(tmp_path, modified, modified)
    with pytest.raises(ValueError, match="MANDATORY_BASELINE_CASE"):
        validate_profile_baseline(tmp_path, partial)


def test_rehashed_declared_partial_diff_cannot_hide_actual_product_change(tmp_path: Path) -> None:
    baseline = baseline_fixture(tmp_path)
    changed_tool(tmp_path)
    partial = profile_bundle(tmp_path, baseline, baseline)
    partial["run_evidence"]["selection"]["changed_files"] = []
    from scripts.verification_profiles import canonical_digest

    partial["verification"]["selection_digest"] = canonical_digest(
        partial["run_evidence"]["selection"]
    )
    partial["verification"]["verification_receipt_id"] = calculate_verification_receipt_id(
        partial["verification"]
    )
    partial["bundle_id"] = digest({k: v for k, v in partial.items() if k != "bundle_id"})
    with pytest.raises(ValueError, match="ACTUAL_DIFF_SELECTION"):
        validate_profile_baseline(tmp_path, partial)
    (tmp_path / "source.py").write_text("product_changed = True\n", encoding="utf-8")
    selection, _ = select_current(tmp_path, capture_source_manifest(tmp_path), ())
    assert selection["profile"] == "FULL"


def test_missing_or_corrupt_baseline_never_authorizes_tooling(tmp_path: Path) -> None:
    baseline = baseline_fixture(tmp_path)
    path = tmp_path / ".codex/verification/bundles" / (baseline["bundle_id"] + ".json")
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        read_bundle(tmp_path, baseline["bundle_id"])
    with pytest.raises(ValueError):
        select_current(tmp_path, capture_source_manifest(tmp_path), ())


def test_candidate_selector_cannot_exempt_its_own_source_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import verification_profile_contract as contract
    from scripts.verification_profiles import canonical_digest

    baseline = baseline_fixture(tmp_path)
    changed_tool(tmp_path)
    partial = profile_bundle(tmp_path, baseline, baseline)
    (tmp_path / "scripts/verification_profiles.py").write_text(
        "# candidate attempts to waive its own policy check\n", encoding="utf-8"
    )
    partial["source_manifest"] = capture_source_manifest(tmp_path)
    partial["run_evidence"]["selection"]["current_source_digest"] = partial["source_manifest"][
        "repository_digest"
    ]
    partial["verification"]["selection_digest"] = canonical_digest(
        partial["run_evidence"]["selection"]
    )

    original_selector = contract.select_manifest_profile

    def fake_selector(current: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        if current["repository_digest"] == partial["source_manifest"]["repository_digest"]:
            return partial["run_evidence"]["selection"]
        return original_selector(current, **kwargs)

    monkeypatch.setattr(contract, "select_manifest_profile", fake_selector)
    with pytest.raises(ValueError, match="PRIOR_POLICY_REQUIRES_FULL"):
        validate_profile_baseline(tmp_path, partial)


def test_new_environment_cannot_reuse_tooling_baseline(tmp_path: Path) -> None:
    from tests.architecture.profile_helpers import RUNTIME

    baseline_fixture(tmp_path)
    changed_tool(tmp_path)
    selected, _ = select_current(
        tmp_path,
        capture_source_manifest(tmp_path),
        (),
        runtime={**RUNTIME, "pytest_version": "different"},
    )
    assert selected["profile"] == "FULL"
    assert "BASELINE_RUNTIME_CHANGED" in selected["reason_codes"]


def test_partial_profile_cannot_be_relabelled_full(tmp_path: Path) -> None:
    from scripts.verification_bundle_contract import validate_bundle

    baseline = baseline_fixture(tmp_path)
    changed_tool(tmp_path)
    partial = profile_bundle(tmp_path, baseline, baseline)
    partial["verification"]["verification_profile"] = "FULL"
    partial["verification"]["full_suite_verified"] = True
    partial["verification"]["verification_receipt_id"] = calculate_verification_receipt_id(
        partial["verification"]
    )
    partial["bundle_id"] = digest({k: v for k, v in partial.items() if k != "bundle_id"})
    with pytest.raises(ValueError, match="PROFILE_"):
        validate_bundle(partial)


def test_partial_cannot_downgrade_to_legacy_full_format(tmp_path: Path) -> None:
    from scripts.verification_bundle_contract import validate_bundle

    baseline = baseline_fixture(tmp_path)
    changed_tool(tmp_path)
    partial = profile_bundle(tmp_path, baseline, baseline)
    for key in ("verification_profile", "full_suite_verified", "selection_digest"):
        partial["verification"].pop(key)
    partial["verification"]["verification_receipt_id"] = calculate_verification_receipt_id(
        partial["verification"]
    )
    for key in ("selection", "observations"):
        partial["run_evidence"].pop(key)
    partial["run_evidence"].update(
        kind="FULL_VERIFY_SUBPROCESS", command=["Makefile.ps1", "verify"]
    )
    partial["run_evidence"]["runtime"] = {
        k: v
        for k, v in partial["run_evidence"]["runtime"].items()
        if k in {"python_version", "platform", "shell"}
    }
    partial["proof_scope"] = "LOCAL_WORKFLOW_VERIFICATION"
    partial["bundle_id"] = digest({k: v for k, v in partial.items() if k != "bundle_id"})
    with pytest.raises(ValueError, match="PROFILE_METADATA_REQUIRED"):
        validate_bundle(partial)
