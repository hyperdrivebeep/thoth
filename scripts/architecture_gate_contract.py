from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, cast

from scripts.architecture_contract import ROOT, acceptance_ids, load_manifest, rule_bundle_digest
from scripts.required_architecture_checks import validate_check_results
from scripts.verification_identity import GENERATED_ROOT, is_generated_verification_path

PREFLIGHT_FIELDS = (
    "schema_version",
    "acceptance_id",
    "mode",
    "declared_scope",
    "planned_remediations",
    "rule_bundle_digest",
    "baseline_checks",
    "note",
    "status",
    "created_at",
)
VERIFICATION_FIELDS = (
    "schema_version",
    "preflight_receipt_id",
    "acceptance_id",
    "rule_bundle_digest",
    "scope_digest",
    "status",
    "verified_at",
)
CANCELLATION_FIELDS = (
    "schema_version",
    "preflight_receipt_id",
    "status",
    "cancel_reason",
    "cancelled_at",
)
WIKI_SYNC_FIELDS = (
    "schema_version",
    "preflight_receipt_id",
    "acceptance_id",
    "state",
    "updated_paths",
    "no_change_reason",
    "recorded_at",
)


def _canonical_hash(value: dict[str, object]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _require_sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"{field} must be a SHA-256 hex digest")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError(f"{field} must be a SHA-256 hex digest") from exc
    return value


def _preflight_draft(value: dict[str, object]) -> dict[str, object]:
    draft: dict[str, object] = {}
    for field in PREFLIGHT_FIELDS:
        if field not in value:
            raise ValueError(f"preflight is missing required field: {field}")
        draft[field] = value[field]
    draft["status"] = "READY_FOR_EDIT"
    context = {key: value[key] for key in ("plan_id", "node_id") if key in value}
    if context:
        if set(context) != {"plan_id", "node_id"} or any(
            not isinstance(item, str) or not item or len(item) > 100 for item in context.values()
        ):
            raise ValueError("preflight plan/node context is invalid")
        draft.update(context)
    if "rule_snapshot_id" in value:
        draft["rule_snapshot_id"] = _require_sha256(
            value["rule_snapshot_id"], field="rule_snapshot_id"
        )
    if "rule_change_proposal_id" in value:
        draft["rule_change_proposal_id"] = _require_sha256(
            value["rule_change_proposal_id"], field="rule_change_proposal_id"
        )
    if "owner_context" in value:
        from scripts.hook_owner_contract import validate_owner_context

        draft["owner_context"] = validate_owner_context(value["owner_context"])
    return draft


def calculate_preflight_receipt_id(value: dict[str, object]) -> str:
    return _canonical_hash(_preflight_draft(value))


def preflight_archive_payload(value: dict[str, object]) -> dict[str, object]:
    draft = _preflight_draft(value)
    receipt_id = _require_sha256(value.get("preflight_receipt_id"), field="preflight_receipt_id")
    return {**draft, "preflight_receipt_id": receipt_id}


def validate_preflight(
    value: dict[str, object],
    *,
    allowed_statuses: frozenset[str],
) -> tuple[str, ...]:
    scopes = validate_preflight_identity(value, allowed_statuses=allowed_statuses)
    manifest = load_manifest()
    if value.get("acceptance_id") not in acceptance_ids(manifest):
        raise ValueError("preflight Acceptance ID is invalid")
    if value.get("rule_bundle_digest") != rule_bundle_digest(manifest):
        raise ValueError("preflight rule bundle digest is invalid")
    if value.get("rule_change_proposal_id") is not None:
        from scripts.rule_transaction_contract import require_publication

        override = os.environ.get("THOTH_PREFLIGHT_PATH")
        gate = (
            Path(override).parent if override
            else Path(os.environ.get("THOTH_GATE_DIR", ROOT / ".thoth/architecture"))
        )
        require_publication(gate, value)
    return scopes


def validate_preflight_identity(
    value: dict[str, object],
    *,
    allowed_statuses: frozenset[str],
) -> tuple[str, ...]:
    """Historical identity only. Never use this function to authorize product edits."""
    if not isinstance(value, dict):
        raise ValueError("preflight must be an object")
    status = value.get("status")
    if status not in allowed_statuses:
        raise ValueError(f"preflight status is not allowed: {status}")
    if value.get("mode") != "IMPLEMENTATION":
        raise ValueError("preflight mode must be IMPLEMENTATION")
    if value.get("schema_version") != "1.0.0":
        raise ValueError("unsupported preflight schema version")

    acceptance = value.get("acceptance_id")
    if not isinstance(acceptance, str) or acceptance not in {
        f"A{number:02d}" for number in range(1, 14)
    }:
        raise ValueError("preflight Acceptance ID is invalid")
    _require_sha256(value.get("rule_bundle_digest"), field="rule_bundle_digest")

    raw_scopes: object = value.get("declared_scope")
    if not isinstance(raw_scopes, list) or not raw_scopes:
        raise ValueError("preflight declared_scope must be a non-empty list")
    scopes: list[str] = []
    for raw in cast(list[object], raw_scopes):
        if not isinstance(raw, str) or not raw:
            raise ValueError("preflight scope is invalid")
        candidate = (ROOT / raw).resolve()
        try:
            normalized = candidate.relative_to(ROOT).as_posix()
        except ValueError as exc:
            raise ValueError(f"preflight scope escapes repository: {raw}") from exc
        if normalized != raw.replace("\\", "/"):
            raise ValueError(f"preflight scope is not canonical: {raw}")
        scopes.append(normalized)
    if len(scopes) != len(set(scopes)):
        raise ValueError("preflight scopes contain duplicates")

    if not isinstance(value.get("planned_remediations"), list):
        raise ValueError("preflight planned_remediations must be a list")
    if not isinstance(value.get("baseline_checks"), list):
        raise ValueError("preflight baseline_checks must be a list")
    validate_check_results(value["baseline_checks"])
    if not isinstance(value.get("note"), str):
        raise ValueError("preflight note must be text")
    if not isinstance(value.get("created_at"), str) or not value["created_at"]:
        raise ValueError("preflight created_at is invalid")

    receipt_id = _require_sha256(value.get("preflight_receipt_id"), field="preflight_receipt_id")
    if receipt_id != calculate_preflight_receipt_id(value):
        raise ValueError("preflight receipt identity is invalid")
    return tuple(scopes)


def calculate_verification_receipt_id(value: dict[str, object]) -> str:
    draft: dict[str, object] = {}
    for field in VERIFICATION_FIELDS:
        if field not in value:
            raise ValueError(f"verification is missing required field: {field}")
        draft[field] = value[field]
    if "wiki_sync_receipt_id" in value:
        draft["wiki_sync_receipt_id"] = value["wiki_sync_receipt_id"]
    for field in (
        "repository_digest",
        "index_digest",
        "source_manifest_digest",
        "source_manifest_policy",
        "verification_profile",
        "full_suite_verified",
        "selection_digest",
    ):
        if field in value:
            draft[field] = value[field]
    return _canonical_hash(draft)


def calculate_wiki_sync_receipt_id(value: dict[str, object]) -> str:
    draft: dict[str, object] = {}
    for field in WIKI_SYNC_FIELDS:
        if field not in value:
            raise ValueError(f"wiki sync is missing required field: {field}")
        draft[field] = value[field]
    return _canonical_hash(draft)


def validate_wiki_sync(
    value: dict[str, object],
    *,
    preflight: dict[str, object],
) -> None:
    if value.get("schema_version") != "1.0.0":
        raise ValueError("unsupported wiki-sync schema version")
    if value.get("preflight_receipt_id") != preflight.get("preflight_receipt_id"):
        raise ValueError("wiki-sync preflight identity does not match")
    if value.get("acceptance_id") != preflight.get("acceptance_id"):
        raise ValueError("wiki-sync Acceptance ID does not match")
    state = value.get("state")
    updated = value.get("updated_paths")
    reason = value.get("no_change_reason")
    if not isinstance(updated, list):
        raise ValueError("wiki-sync updated_paths must be a list")
    if state == "UPDATED":
        if not updated or reason is not None:
            raise ValueError("UPDATED wiki-sync requires paths and no NO_CHANGE reason")
    elif state == "NO_CHANGE":
        if updated or not isinstance(reason, str) or not reason.strip():
            raise ValueError("NO_CHANGE wiki-sync requires a reason and no updated paths")
    else:
        raise ValueError("wiki-sync state must be UPDATED or NO_CHANGE")
    if not isinstance(value.get("recorded_at"), str) or not value["recorded_at"]:
        raise ValueError("wiki-sync recorded_at is invalid")
    receipt_id = _require_sha256(value.get("wiki_sync_receipt_id"), field="wiki_sync_receipt_id")
    if receipt_id != calculate_wiki_sync_receipt_id(value):
        raise ValueError("wiki-sync receipt identity is invalid")
    seen: set[str] = set()
    for entry in updated:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise ValueError("wiki-sync path entry is invalid")
        relative = entry["path"]
        path = (ROOT / relative).resolve()
        allowed = (ROOT / "PROJECT_WIKI", ROOT / "docs/verification", ROOT / "research-briefs")
        if (
            not any(folder.resolve() in path.parents for folder in allowed)
            and path != ROOT / "AGENTS.md"
        ):
            raise ValueError("wiki-sync path escapes canonical truth surfaces")
        if relative in seen or path.relative_to(ROOT).as_posix() != relative:
            raise ValueError("wiki-sync path is duplicate or noncanonical")
        seen.add(relative)
        if not path.is_file():
            raise ValueError("wiki-sync file is missing")
        data = path.read_bytes()
        if (
            entry.get("size") != len(data)
            or entry.get("sha256") != hashlib.sha256(data).hexdigest()
        ):
            raise ValueError("wiki-sync file changed after recording")


def validate_verification(
    value: dict[str, object],
    *,
    preflight: dict[str, object],
) -> None:
    from scripts.verification_profile_contract import receipt_profile

    receipt_profile(value)
    if value.get("schema_version") != "1.0.0" or value.get("status") != "PASS":
        raise ValueError("verification status or schema is invalid")
    for field in ("preflight_receipt_id", "acceptance_id", "rule_bundle_digest"):
        if value.get(field) != preflight.get(field):
            raise ValueError(f"verification {field} does not match preflight")
    _require_sha256(value.get("scope_digest"), field="scope_digest")
    receipt_id = _require_sha256(
        value.get("verification_receipt_id"), field="verification_receipt_id"
    )
    if receipt_id != calculate_verification_receipt_id(value):
        raise ValueError("verification receipt identity is invalid")
    if preflight.get("verification_receipt_id") != receipt_id:
        raise ValueError("verified preflight does not reference verification receipt")
    if preflight.get("verified_at") != value.get("verified_at"):
        raise ValueError("verified preflight timestamp does not match verification receipt")


def calculate_cancellation_receipt_id(value: dict[str, object]) -> str:
    draft: dict[str, object] = {}
    for field in CANCELLATION_FIELDS:
        if field not in value:
            raise ValueError(f"cancellation is missing required field: {field}")
        draft[field] = value[field]
    return _canonical_hash(draft)


def validate_cancellation(value: dict[str, object]) -> None:
    if value.get("status") != "CANCELLED":
        raise ValueError("preflight is not cancelled")
    if not isinstance(value.get("cancel_reason"), str) or not value["cancel_reason"]:
        raise ValueError("cancellation reason is missing")
    if not isinstance(value.get("cancelled_at"), str) or not value["cancelled_at"]:
        raise ValueError("cancellation timestamp is missing")
    receipt_id = _require_sha256(
        value.get("cancellation_receipt_id"), field="cancellation_receipt_id"
    )
    if receipt_id != calculate_cancellation_receipt_id(value):
        raise ValueError("cancellation receipt identity is invalid")


def scope_digest(scopes: list[str]) -> str:
    digest = hashlib.sha256()
    for relative in sorted(scopes):
        if is_generated_verification_path(relative):
            continue
        path = (ROOT / relative).resolve()
        if path.is_file():
            digest.update(relative.encode())
            digest.update(path.read_bytes())
        elif path.is_dir():
            for child in sorted(
                item
                for item in path.rglob("*")
                if item.is_file()
                and "__pycache__" not in item.parts
                and item.suffix != ".pyc"
                and not is_generated_verification_path(item.relative_to(ROOT).as_posix())
            ):
                digest.update(child.relative_to(ROOT).as_posix().encode())
                digest.update(child.read_bytes())
        else:
            if relative not in {
                GENERATED_ROOT,
                GENERATED_ROOT + "/bundles",
                GENERATED_ROOT + "/indices",
            }:
                digest.update(f"MISSING:{relative}".encode())
    return digest.hexdigest()


def archive_receipt(
    gate_dir: Path,
    *,
    receipt_id: str,
    kind: str,
    payload: dict[str, Any],
) -> Path:
    _require_sha256(receipt_id, field=f"{kind}_receipt_id")
    directory = gate_dir / "receipts"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{receipt_id}.{kind}.json"
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError:
        if path.read_bytes() != encoded:
            raise ValueError(f"archived {kind} receipt payload conflicts: {receipt_id}") from None
        return path
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    return path


def validate_archived_receipt(
    gate_dir: Path,
    *,
    receipt_id: str,
    kind: str,
    payload: dict[str, object],
) -> None:
    _require_sha256(receipt_id, field=f"{kind}_receipt_id")
    path = gate_dir / "receipts" / f"{receipt_id}.{kind}.json"
    if not path.is_file():
        raise ValueError(f"archived {kind} receipt is missing: {receipt_id}")
    try:
        archived = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"archived {kind} receipt is unreadable: {receipt_id}") from exc
    if archived != payload:
        raise ValueError(f"archived {kind} receipt does not match pointer: {receipt_id}")
