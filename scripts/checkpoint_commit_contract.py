from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from scripts.architecture_gate_contract import (
    archive_receipt,
    preflight_archive_payload,
    scope_digest,
    validate_archived_receipt,
    validate_preflight,
    validate_verification,
    validate_wiki_sync,
)
from scripts.verification_bundle_contract import require_portable_receipt
from scripts.verification_identity import repository_digest

CHECKPOINT_FIELDS = (
    "schema_version",
    "preflight_receipt_id",
    "verification_receipt_id",
    "parent_commit",
    "staged_tree",
    "remote_name",
    "remote_url",
    "branch",
    "created_at",
)


def _hash(value: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        raise ValueError(f"Git checkpoint probe failed: {' '.join(args)}")
    return completed.stdout.rstrip("\r\n")


def _gate(root: Path, gate_dir: Path) -> tuple[dict[str, object], dict[str, object]]:
    preflight_path = gate_dir / "preflight.json"
    verification_path = gate_dir / "verification.json"
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    validate_preflight(preflight, allowed_statuses=frozenset({"VERIFIED"}))
    validate_archived_receipt(
        gate_dir,
        receipt_id=str(preflight["preflight_receipt_id"]),
        kind="preflight",
        payload=preflight_archive_payload(preflight),
    )
    verification = json.loads(verification_path.read_text(encoding="utf-8"))
    validate_verification(verification, preflight=preflight)
    validate_archived_receipt(
        gate_dir,
        receipt_id=str(verification["verification_receipt_id"]),
        kind="verification",
        payload=verification,
    )
    if verification["scope_digest"] != scope_digest(list(preflight["declared_scope"])):
        raise ValueError("verified scope changed after verification")
    if verification.get("repository_digest") != repository_digest(root):
        raise ValueError("repository source changed or verification source identity is missing")
    wiki_path = gate_dir / "wiki-sync" / f"{preflight['preflight_receipt_id']}.json"
    wiki = json.loads(wiki_path.read_text(encoding="utf-8"))
    validate_wiki_sync(wiki, preflight=preflight)
    if wiki.get("wiki_sync_receipt_id") != verification.get("wiki_sync_receipt_id"):
        raise ValueError("verification wiki-sync identity mismatch")
    validate_archived_receipt(
        gate_dir, receipt_id=str(wiki["wiki_sync_receipt_id"]), kind="wiki-sync", payload=wiki
    )
    require_portable_receipt(root, verification)
    return preflight, verification


def _status(root: Path) -> tuple[str, ...]:
    return tuple(_git(root, "status", "--porcelain=v1").splitlines())


def _remote_url(root: Path, remote: str) -> str:
    urls = _git(root, "remote", "get-url", "--push", "--all", remote).splitlines()
    if len(urls) != 1 or not urls[0]:
        raise ValueError("checkpoint requires exactly one effective push remote URL")
    return urls[0]


def _fully_staged(root: Path) -> bool:
    records = _status(root)
    return bool(records) and all(
        len(row) >= 3 and row[0] in "MADRC" and row[1] == " " for row in records
    )


def _clean(root: Path) -> bool:
    return not _status(root)


def _draft(value: dict[str, object]) -> dict[str, object]:
    return {field: value[field] for field in CHECKPOINT_FIELDS}


def validate_checkpoint_identity(value: dict[str, object]) -> None:
    if value.get("schema_version") != "1.0.0":
        raise ValueError("unsupported checkpoint schema")
    expected = _hash(_draft(value))
    if value.get("checkpoint_receipt_id") != expected:
        raise ValueError("checkpoint receipt identity is invalid")


def prepare_checkpoint(
    *, root: Path, gate_dir: Path, remote_name: str, branch: str
) -> dict[str, object]:
    preflight, verification = _gate(root, gate_dir)
    if not _fully_staged(root):
        raise ValueError("checkpoint requires a non-empty fully staged working tree")
    if _git(root, "branch", "--show-current") != branch:
        raise ValueError("checkpoint branch does not match current branch")
    draft: dict[str, object] = {
        "schema_version": "1.0.0",
        "preflight_receipt_id": preflight["preflight_receipt_id"],
        "verification_receipt_id": verification["verification_receipt_id"],
        "parent_commit": _git(root, "rev-parse", "HEAD"),
        "staged_tree": _git(root, "write-tree"),
        "remote_name": remote_name,
        "remote_url": _remote_url(root, remote_name),
        "branch": branch,
        "created_at": datetime.now(UTC).isoformat(),
    }
    receipt_id = _hash(draft)
    payload = {**draft, "checkpoint_receipt_id": receipt_id}
    archive_receipt(gate_dir, receipt_id=receipt_id, kind="checkpoint", payload=payload)
    pointer = gate_dir / "checkpoint.json"
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    temporary = pointer.with_suffix(".tmp")
    temporary.write_bytes(encoded)
    os.replace(temporary, pointer)
    return payload


def validate_checkpoint_action(
    *,
    root: Path,
    gate_dir: Path,
    action: str,
    remote_name: str | None = None,
    branch: str | None = None,
) -> None:
    preflight, verification = _gate(root, gate_dir)
    checkpoint = json.loads((gate_dir / "checkpoint.json").read_text(encoding="utf-8"))
    validate_checkpoint_identity(checkpoint)
    validate_archived_receipt(
        gate_dir,
        receipt_id=str(checkpoint["checkpoint_receipt_id"]),
        kind="checkpoint",
        payload=checkpoint,
    )
    if (
        checkpoint["preflight_receipt_id"] != preflight["preflight_receipt_id"]
        or checkpoint["verification_receipt_id"] != verification["verification_receipt_id"]
    ):
        raise ValueError("checkpoint does not bind the active verification")
    if _git(root, "branch", "--show-current") != checkpoint["branch"]:
        raise ValueError("checkpoint branch changed")
    if _remote_url(root, str(checkpoint["remote_name"])) != checkpoint["remote_url"]:
        raise ValueError("checkpoint remote URL changed")
    if action == "commit":
        if not _fully_staged(root):
            raise ValueError("checkpointed commit requires a fully staged tree")
        if _git(root, "rev-parse", "HEAD") != checkpoint["parent_commit"]:
            raise ValueError("checkpoint parent commit changed")
        if _git(root, "write-tree") != checkpoint["staged_tree"]:
            raise ValueError("staged tree changed after checkpoint")
        return
    if action != "push":
        raise ValueError("unsupported checkpoint action")
    if remote_name != checkpoint["remote_name"] or branch != checkpoint["branch"]:
        raise ValueError("push target differs from checkpoint")
    if _remote_url(root, str(remote_name)) != checkpoint["remote_url"]:
        raise ValueError("push remote URL changed")
    if _git(root, "branch", "--show-current") != branch:
        raise ValueError("push branch changed")
    if not _clean(root):
        raise ValueError("push requires a clean working tree")
    if _git(root, "rev-parse", "HEAD^{tree}") != checkpoint["staged_tree"]:
        raise ValueError("HEAD tree differs from verified checkpoint")
    if _git(root, "rev-parse", "HEAD^") != checkpoint["parent_commit"]:
        raise ValueError("checkpoint commit is not the current HEAD successor")
