from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from scripts import architecture_gate_contract as gate
from scripts import complete_architecture_gate as completion
from scripts.verification_bundle_contract import (
    build_bundle,
    current_index,
    digest,
    portable_store_digest,
    publish_bundle,
    read_bundle,
    require_portable_receipt,
    validate_bundle,
)
from scripts.verification_identity import capture_source_manifest, repository_digest, source_matches
from scripts.verification_status import verification_status
from tests.architecture.progress_helpers import simulated_reports

ROOT = Path(__file__).resolve().parents[2]


def fixture_bundle(root: Path, *, owned: bool = False) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    (root / ".gitignore").write_text(".thoth/\n", encoding="utf-8")
    (root / "source.py").write_text("value = 1\n", encoding="utf-8")
    (root / "fixture-rule.md").write_text("immutable fixture rule\n", encoding="utf-8")
    fixture_test = root / "tests/architecture/test_fixture.py"
    fixture_test.parent.mkdir(parents=True)
    fixture_test.write_text("def test_fixture():\n    assert True\n", encoding="utf-8")
    wiki_path = root / "PROJECT_WIKI/NOW.md"
    wiki_path.parent.mkdir()
    wiki_path.write_text("Fixture evidence only.\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@invalid.local",
            "commit",
            "--no-gpg-sign",
            "-qm",
            "fixture",
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )
    preflight = gate.preflight_archive_payload(
        json.loads((ROOT / ".thoth/architecture/preflight.json").read_text(encoding="utf-8"))
    )
    preflight.pop("owner_context", None)  # This fixture exercises legacy portable identity.
    preflight.update(
        {
            "acceptance_id": "A11",
            "declared_scope": ["source.py", "PROJECT_WIKI", ".codex/verification"],
            "plan_id": "FIXTURE_ONLY",
            "node_id": "FIXTURE_NODE",
        }
    )
    if owned:
        from tests.architecture.test_stop_session_ownership import owner_record

        preflight["owner_context"] = owner_record()
        preflight.pop("plan_id")
        preflight.pop("node_id")
    preflight["preflight_receipt_id"] = gate.calculate_preflight_receipt_id(preflight)
    timestamp = datetime.now(UTC).isoformat()
    data = wiki_path.read_bytes()
    wiki: dict[str, Any] = {
        "schema_version": "1.0.0",
        "preflight_receipt_id": preflight["preflight_receipt_id"],
        "acceptance_id": "A11",
        "state": "UPDATED",
        "no_change_reason": None,
        "recorded_at": timestamp,
        "updated_paths": [
            {
                "path": "PROJECT_WIKI/NOW.md",
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
            }
        ],
    }
    wiki["wiki_sync_receipt_id"] = gate.calculate_wiki_sync_receipt_id(wiki)
    manifest = capture_source_manifest(root)
    verification = {
        "schema_version": "1.0.0",
        "preflight_receipt_id": preflight["preflight_receipt_id"],
        "acceptance_id": "A11",
        "rule_bundle_digest": preflight["rule_bundle_digest"],
        "scope_digest": "a" * 64,
        "repository_digest": manifest["repository_digest"],
        "index_digest": manifest["index_digest"],
        "wiki_sync_receipt_id": wiki["wiki_sync_receipt_id"],
        "source_manifest_digest": manifest["manifest_digest"],
        "source_manifest_policy": manifest["policy"],
        "status": "PASS",
        "verified_at": timestamp,
    }
    verification["verification_receipt_id"] = gate.calculate_verification_receipt_id(verification)
    return build_bundle(
        preflight=preflight,
        verification=verification,
        wiki=wiki,
        source_manifest=manifest,
        run_evidence={
            "kind": "FULL_VERIFY_SUBPROCESS",
            "command": ["Makefile.ps1", "verify"],
            "exit_code": 0,
            "started_at": timestamp,
            "finished_at": timestamp,
        },
    )


def test_published_bundle_reopens_without_ignored_runtime_state(tmp_path: Path) -> None:
    original = tmp_path / "original"
    bundle = fixture_bundle(original)
    publish_bundle(original, bundle, expected_index_id=None)
    clone = tmp_path / "clone"
    shutil.copytree(original, clone, ignore=shutil.ignore_patterns(".thoth"))
    result = verification_status(clone)
    assert result["status"] == "COMPLETED_RECORD"
    assert result["current_source_verified"] is True
    assert result["active_local_gate"] is None
    assert result["plan_id"] == "FIXTURE_ONLY"
    assert result["node_id"] == "FIXTURE_NODE"
    require_portable_receipt(clone, bundle["verification"])
    (clone / "source.py").write_text("value = 2\n", encoding="utf-8")
    assert verification_status(clone)["current_source_verified"] is False
    with pytest.raises(ValueError, match="SOURCE"):
        require_portable_receipt(clone, bundle["verification"])


@pytest.mark.parametrize("point", ["after_bundle", "after_index"])
def test_publication_fault_leaves_no_current_and_retry_is_explicit(
    tmp_path: Path, point: str
) -> None:
    bundle = fixture_bundle(tmp_path)

    def fail(at: str) -> None:
        if at == point:
            raise RuntimeError("injected publication fault")

    with pytest.raises(RuntimeError, match="injected"):
        publish_bundle(tmp_path, bundle, expected_index_id=None, fault=fail)
    assert current_index(tmp_path) is None
    portable_store_digest(tmp_path)
    assert verification_status(tmp_path)["status"] == "NO_PORTABLE_COMPLETION"
    published = publish_bundle(tmp_path, bundle, expected_index_id=None)
    assert published is not None and published["generation"] == 1
    assert source_matches(tmp_path, bundle["source_manifest"])


def test_current_index_cas_and_parent_integrity(tmp_path: Path) -> None:
    bundle = fixture_bundle(tmp_path)
    first = publish_bundle(tmp_path, bundle, expected_index_id=None)
    assert first is not None
    with pytest.raises(ValueError, match="CONFLICT"):
        publish_bundle(tmp_path, bundle, expected_index_id=None)
    second = publish_bundle(tmp_path, bundle, expected_index_id=first["index_id"])
    assert second is not None and second["generation"] == 2
    parent = tmp_path / ".codex/verification/indices" / (first["index_id"] + ".json")
    value = json.loads(parent.read_text(encoding="utf-8"))
    value["verification_receipt_id"] = "0" * 64
    parent.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="INDEX"):
        current_index(tmp_path)


@pytest.mark.parametrize("part", ["source_manifest", "wiki_sync", "verification"])
def test_rehashing_bundle_does_not_hide_changed_bound_proof(tmp_path: Path, part: str) -> None:
    bundle = fixture_bundle(tmp_path)
    altered = copy.deepcopy(bundle)
    if part == "source_manifest":
        altered[part]["files"][0]["sha256"] = "0" * 64
    elif part == "wiki_sync":
        altered[part]["updated_paths"][0]["sha256"] = "0" * 64
    else:
        altered[part]["source_manifest_digest"] = "0" * 64
    altered["bundle_id"] = digest(
        {key: value for key, value in altered.items() if key != "bundle_id"}
    )
    with pytest.raises(ValueError):
        validate_bundle(altered)


def test_source_drift_at_publication_does_not_update_current(tmp_path: Path) -> None:
    bundle = fixture_bundle(tmp_path)

    def drift(at: str) -> None:
        if at == "after_index":
            (tmp_path / "source.py").write_text("changed\n", encoding="utf-8")

    with pytest.raises(ValueError, match="SOURCE"):
        publish_bundle(tmp_path, bundle, expected_index_id=None, fault=drift)
    assert current_index(tmp_path) is None


def test_corrupt_bundle_is_not_hidden_by_source_exclusion(tmp_path: Path) -> None:
    bundle = fixture_bundle(tmp_path)
    publish_bundle(tmp_path, bundle, expected_index_id=None)
    path = tmp_path / ".codex/verification/bundles" / (bundle["bundle_id"] + ".json")
    path.write_text("{}\n", encoding="utf-8")
    assert source_matches(tmp_path, bundle["source_manifest"])
    with pytest.raises(ValueError, match="BUNDLE"):
        read_bundle(tmp_path, bundle["bundle_id"])
    with pytest.raises(ValueError):
        require_portable_receipt(tmp_path, bundle["verification"])


def test_staging_or_checkpoint_commit_is_not_reported_as_a_fresh_test_run(tmp_path: Path) -> None:
    bundle = fixture_bundle(tmp_path)
    publish_bundle(tmp_path, bundle, expected_index_id=None)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@invalid.local",
            "commit",
            "--no-gpg-sign",
            "-qm",
            "record fixture proof",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    result = verification_status(tmp_path)
    assert result["current_source_verified"] is True
    assert result["same_head"] is False
    assert result["tested_head"] == bundle["source_manifest"]["head"]


def test_generated_directory_birth_does_not_change_scope_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = fixture_bundle(tmp_path)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    scopes = ["source.py", "PROJECT_WIKI", ".codex/verification"]
    before = gate.scope_digest(scopes)
    publish_bundle(tmp_path, bundle, expected_index_id=None)
    assert gate.scope_digest(scopes) == before


def prepare_completion_fixture(
    root: Path, monkeypatch: pytest.MonkeyPatch, *, owned: bool = False
) -> dict[str, Any]:
    bundle = fixture_bundle(root, owned=owned)
    gate_dir = root / ".thoth/architecture"
    if owned:
        owner = bundle["preflight"]["owner_context"]
        gate.archive_receipt(
            gate_dir, receipt_id=owner["owner_receipt_id"], kind="host-session", payload=owner
        )
    for kind, value, identifier in (
        ("preflight", bundle["preflight"], bundle["preflight"]["preflight_receipt_id"]),
        ("wiki-sync", bundle["wiki_sync"], bundle["wiki_sync"]["wiki_sync_receipt_id"]),
    ):
        gate.archive_receipt(gate_dir, receipt_id=identifier, kind=kind, payload=value)
    (gate_dir / "preflight.json").write_text(json.dumps(bundle["preflight"]), encoding="utf-8")
    sync_dir = gate_dir / "wiki-sync"
    sync_dir.mkdir()
    (sync_dir / (bundle["preflight"]["preflight_receipt_id"] + ".json")).write_text(
        json.dumps(bundle["wiki_sync"]), encoding="utf-8"
    )
    monkeypatch.setattr(gate, "ROOT", root)
    for name, value in {
        "ROOT": root,
        "GATE_DIR": gate_dir,
        "PREFLIGHT": gate_dir / "preflight.json",
        "VERIFICATION": gate_dir / "verification.json",
    }.items():
        monkeypatch.setattr(completion, name, value)
    return bundle


def test_completion_entry_publishes_bundle_before_marking_active_gate_verified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_completion_fixture(tmp_path, monkeypatch)
    calls: list[list[str]] = []

    def completed(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        assert args[-1] == "verify"
        assert "-ExecutionPolicy" not in args
        assert kwargs["cwd"] == tmp_path
        assert current_index(tmp_path) is None
        simulated_reports(tmp_path, kwargs["env"]["THOTH_VERIFICATION_RUN_ID"])
        return subprocess.CompletedProcess(args, 0)

    # Exercise the completion state machine; this simulated subprocess is test evidence only.
    monkeypatch.setattr(completion, "subprocess", SimpleNamespace(run=completed))
    assert completion.main() == 0
    assert len(calls) == 1
    assert calls[0][-2:] == [str(tmp_path / "Makefile.ps1"), "verify"]
    preflight = json.loads(
        (tmp_path / ".thoth/architecture/preflight.json").read_text(encoding="utf-8")
    )
    verification = json.loads(
        (tmp_path / ".thoth/architecture/verification.json").read_text(encoding="utf-8")
    )
    assert preflight["status"] == "VERIFIED"
    require_portable_receipt(tmp_path, verification)
    assert verification_status(tmp_path)["active_preflight_matches"] is True


@pytest.mark.parametrize(
    "failure", ["exit", "source", "index", "rules", "never_started", "preflight", "wiki"]
)
def test_completion_failure_does_not_publish_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    bundle = prepare_completion_fixture(tmp_path, monkeypatch)

    def completed(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if failure == "source":
            (tmp_path / "source.py").write_text("changed\n", encoding="utf-8")
        elif failure == "rules":
            (tmp_path / "fixture-rule.md").write_text("changed rule\n", encoding="utf-8")
        elif failure == "index":
            subprocess.run(
                ["git", "update-index", "--chmod=+x", "source.py"],
                cwd=tmp_path,
                check=True,
                capture_output=True,
            )
        elif failure == "never_started":
            raise OSError("fixture full verification could not start")
        elif failure == "preflight":
            path = tmp_path / ".thoth/architecture/preflight.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["status"] = "CANCELLED"
            path.write_text(json.dumps(value), encoding="utf-8")
        elif failure == "wiki":
            path = (
                tmp_path
                / ".thoth/architecture/wiki-sync"
                / (bundle["preflight"]["preflight_receipt_id"] + ".json")
            )
            path.write_text("{}", encoding="utf-8")
        return subprocess.CompletedProcess(args, 1 if failure == "exit" else 0)

    monkeypatch.setattr(completion, "subprocess", SimpleNamespace(run=completed))
    with pytest.raises((ValueError, RuntimeError, OSError)):
        completion.main()
    assert current_index(tmp_path) is None
    assert not (tmp_path / ".thoth/architecture/verification.json").exists()


@pytest.mark.parametrize(
    "missing", ["progress.json", "stages.json", "durations.json", "junit-safe.xml"]
)
def test_completion_exit_zero_requires_complete_observations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    prepare_completion_fixture(tmp_path, monkeypatch)
    calls: list[list[str]] = []

    def completed(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        run_id = kwargs["env"]["THOTH_VERIFICATION_RUN_ID"]
        simulated_reports(tmp_path, run_id)
        (tmp_path / ".thoth/verification-runs" / run_id / missing).unlink()
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(completion, "subprocess", SimpleNamespace(run=completed))
    with pytest.raises((ValueError, OSError)):
        completion.main()
    assert len(calls) == 1
    assert current_index(tmp_path) is None
    assert not (tmp_path / ".thoth/architecture/verification.json").exists()
    active = json.loads((tmp_path / ".thoth/architecture/preflight.json").read_text())
    assert active["status"] == "READY_FOR_EDIT"


def test_legacy_import_preserves_old_identity_without_promoting_current(tmp_path: Path) -> None:
    bundle = fixture_bundle(tmp_path)
    verification = copy.deepcopy(bundle["verification"])
    verification.pop("source_manifest_digest")
    verification.pop("source_manifest_policy")
    verification["verification_receipt_id"] = gate.calculate_verification_receipt_id(verification)
    manifest = copy.deepcopy(bundle["source_manifest"])
    manifest["policy"] = "git-reviewable-source-v1"
    historical = build_bundle(
        preflight=bundle["preflight"],
        verification=verification,
        wiki=bundle["wiki_sync"],
        source_manifest=manifest,
        historical=True,
    )
    publish_bundle(tmp_path, historical, expected_index_id=None, historical_only=True)
    assert current_index(tmp_path) is None
    stored = read_bundle(tmp_path, historical["bundle_id"])
    assert (
        stored["verification"]["verification_receipt_id"] == verification["verification_receipt_id"]
    )
    assert stored["proof_scope"] == "HISTORICAL_SOURCE_RECEIPT"


def test_generated_receipt_does_not_hash_itself_but_arbitrary_source_still_does(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / "source.py").write_text("value = 1\n", encoding="utf-8")
    before = repository_digest(tmp_path)
    bundle = tmp_path / ".codex/verification/bundles" / ("a" * 64 + ".json")
    bundle.parent.mkdir(parents=True)
    # Source-domain classification is independent of the separate required proof validation.
    bundle.write_text("{}\n", encoding="utf-8")
    assert repository_digest(tmp_path) == before
    (bundle.parent / "arbitrary.py").write_text("value = 2\n", encoding="utf-8")
    assert repository_digest(tmp_path) != before
