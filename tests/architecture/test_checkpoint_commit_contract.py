from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.architecture_gate_contract import (  # noqa: E402
    archive_receipt,
    calculate_verification_receipt_id,
    scope_digest,
)
from scripts.checkpoint_commit_contract import (  # noqa: E402
    prepare_checkpoint,
    validate_checkpoint_action,
)
from scripts.verification_identity import index_digest, repository_digest  # noqa: E402


def git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


def verified_gate(gate: Path, repo: Path) -> None:
    environment = {**os.environ, "THOTH_GATE_DIR": str(gate)}
    prepared = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/prepare_architecture_preflight.py"),
            "--acceptance",
            "A11",
            "--scope",
            "README.md",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    assert prepared.returncode == 0, prepared.stderr
    recorded = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/record_wiki_sync.py"),
            "--no-change-reason",
            "checkpoint contract fixture",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    assert recorded.returncode == 0, recorded.stderr
    preflight_path = gate / "preflight.json"
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    wiki = json.loads(recorded.stdout)
    verified_at = "2026-09-05T00:00:00+00:00"
    draft: dict[str, object] = {
        "schema_version": "1.0.0",
        "preflight_receipt_id": preflight["preflight_receipt_id"],
        "acceptance_id": "A11",
        "rule_bundle_digest": preflight["rule_bundle_digest"],
        "scope_digest": scope_digest(list(preflight["declared_scope"])),
        "repository_digest": repository_digest(repo),
        "index_digest": index_digest(repo),
        "wiki_sync_receipt_id": wiki["wiki_sync_receipt_id"],
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
    (gate / "verification.json").write_text(json.dumps(verification), encoding="utf-8")
    archive_receipt(
        gate,
        receipt_id=verification_id,
        kind="verification",
        payload=verification,
    )


def repository(path: Path) -> None:
    path.mkdir()
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "THOTH test")
    git(path, "config", "user.email", "thoth@example.invalid")
    (path / "README.md").write_text("base\n", encoding="utf-8")
    git(path, "add", "README.md")
    git(path, "commit", "-m", "base")
    git(path, "remote", "add", "origin", "https://example.invalid/thoth.git")


def test_checkpoint_binds_staged_tree_and_exact_push_target(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    gate = tmp_path / "gate"
    repository(repo)
    (repo / "feature.txt").write_text("candidate\n", encoding="utf-8")
    git(repo, "add", "feature.txt")
    verified_gate(gate, repo)

    checkpoint = prepare_checkpoint(root=repo, gate_dir=gate, remote_name="origin", branch="main")
    validate_checkpoint_action(root=repo, gate_dir=gate, action="commit")

    (repo / "feature.txt").write_text("drift\n", encoding="utf-8")
    git(repo, "add", "feature.txt")
    with pytest.raises(ValueError, match=r"source changed|staged tree changed"):
        validate_checkpoint_action(root=repo, gate_dir=gate, action="commit")

    (repo / "feature.txt").write_text("candidate\n", encoding="utf-8")
    git(repo, "add", "feature.txt")
    assert git(repo, "write-tree") == checkpoint["staged_tree"]
    git(repo, "commit", "-m", "candidate")
    validate_checkpoint_action(
        root=repo, gate_dir=gate, action="push", remote_name="origin", branch="main"
    )
    with pytest.raises(ValueError, match="push target"):
        validate_checkpoint_action(
            root=repo, gate_dir=gate, action="push", remote_name="other", branch="main"
        )


def test_checkpoint_rejects_unstaged_or_post_commit_dirty_state(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    gate = tmp_path / "gate"
    repository(repo)
    (repo / "feature.txt").write_text("unstaged\n", encoding="utf-8")
    verified_gate(gate, repo)
    with pytest.raises(ValueError, match="fully staged"):
        prepare_checkpoint(root=repo, gate_dir=gate, remote_name="origin", branch="main")

    git(repo, "add", "feature.txt")
    prepare_checkpoint(root=repo, gate_dir=gate, remote_name="origin", branch="main")
    git(repo, "commit", "-m", "candidate")
    (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"source changed|clean working tree"):
        validate_checkpoint_action(
            root=repo, gate_dir=gate, action="push", remote_name="origin", branch="main"
        )
