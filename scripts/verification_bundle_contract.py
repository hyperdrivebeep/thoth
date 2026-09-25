"""Immutable portable proof data and a compare-and-swap current index."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from scripts.architecture_gate_contract import (
    calculate_preflight_receipt_id,
    calculate_verification_receipt_id,
    calculate_wiki_sync_receipt_id,
    preflight_archive_payload,
)
from scripts.required_architecture_checks import validate_check_results
from scripts.verification_identity import SOURCE_POLICY, source_matches, validate_source_manifest
from scripts.verification_profile_contract import receipt_profile, validate_profile_run

STORE = Path(".codex/verification")


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _sha(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("PORTABLE_INVALID_DIGEST")
    return value


def _object(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("PORTABLE_INVALID_OBJECT")
    return value


def _date(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("PORTABLE_INVALID_TIMESTAMP")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("PORTABLE_NAIVE_TIMESTAMP")
    return parsed


def _relative(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value or "\0" in value:
        raise ValueError("PORTABLE_INVALID_RELATIVE_PATH")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise ValueError("PORTABLE_ESCAPED_PATH")
    return value


def _legacy_manifest(value: dict[str, Any]) -> dict[str, Any]:
    if value.get("policy") != "git-reviewable-source-v1":
        raise ValueError("PORTABLE_UNSUPPORTED_LEGACY_SOURCE")
    files = value.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("PORTABLE_MISSING_LEGACY_FILES")
    digest_value = hashlib.sha256()
    normalized = []
    for item in files:
        entry = _object(item)
        name = _relative(entry.get("path"))
        sha = entry.get("sha256")
        if sha is not None:
            _sha(sha)
        digest_value.update(name.encode() + b"\0")
        digest_value.update(b"MISSING" if sha is None else bytes.fromhex(sha))
        normalized.append(
            {"path": name, "sha256": sha, **({"size": entry["size"]} if "size" in entry else {})}
        )
    names = [entry["path"] for entry in normalized]
    if names != sorted(set(names)) or digest_value.hexdigest() != value.get("repository_digest"):
        raise ValueError("PORTABLE_LEGACY_SOURCE_DIFFERS")
    return {
        "schema_version": "1.0.0",
        "policy": "git-reviewable-source-v1",
        "head": value.get("head"),
        "repository_digest": digest_value.hexdigest(),
        "files": normalized,
    }


def build_bundle(
    *,
    preflight: dict[str, Any],
    verification: dict[str, Any],
    wiki: dict[str, Any],
    source_manifest: dict[str, Any],
    run_evidence: dict[str, Any] | None = None,
    historical: bool = False,
) -> dict[str, Any]:
    manifest = _legacy_manifest(source_manifest) if historical else copy.deepcopy(source_manifest)
    draft = {
        "schema_version": "1.0.0",
        "kind": "THOTH_PORTABLE_VERIFICATION",
        "proof_scope": "HISTORICAL_SOURCE_RECEIPT" if historical else (
            "LOCAL_TOOLING_VERIFICATION" if receipt_profile(verification) == "TOOLING"
            else "LOCAL_WORKFLOW_VERIFICATION"
        ),
        "preflight": preflight_archive_payload(preflight),
        "verification": copy.deepcopy(verification),
        "wiki_sync": copy.deepcopy(wiki),
        "source_manifest": manifest,
        "run_evidence": copy.deepcopy(run_evidence),
        "created_at": datetime.now(UTC).isoformat(),
    }
    bundle = {**draft, "bundle_id": digest(draft)}
    validate_bundle(bundle)
    return bundle


def validate_bundle(value: dict[str, Any]) -> None:
    expected = {
        "schema_version",
        "kind",
        "proof_scope",
        "preflight",
        "verification",
        "wiki_sync",
        "source_manifest",
        "run_evidence",
        "created_at",
        "bundle_id",
    }
    if (
        set(value) != expected
        or value["schema_version"] != "1.0.0"
        or value["kind"] != "THOTH_PORTABLE_VERIFICATION"
    ):
        raise ValueError("PORTABLE_UNSUPPORTED_BUNDLE")
    _sha(value["bundle_id"])
    if value["bundle_id"] != digest(
        {key: item for key, item in value.items() if key != "bundle_id"}
    ):
        raise ValueError("PORTABLE_BUNDLE_ID_DIFFERS")
    _date(value["created_at"])
    preflight, verification, wiki, manifest = (
        _object(value[key]) for key in ("preflight", "verification", "wiki_sync", "source_manifest")
    )
    if preflight.get("preflight_receipt_id") != calculate_preflight_receipt_id(preflight):
        raise ValueError("PORTABLE_PREFLIGHT_ID_DIFFERS")
    if preflight.get("status") != "READY_FOR_EDIT" or preflight.get("mode") != "IMPLEMENTATION":
        raise ValueError("PORTABLE_PREFLIGHT_STATE_INVALID")
    scopes = preflight.get("declared_scope")
    if not isinstance(scopes, list) or not scopes or len(scopes) != len(set(scopes)):
        raise ValueError("PORTABLE_PREFLIGHT_SCOPE_INVALID")
    for scope in scopes:
        _relative(scope)
    validate_check_results(preflight.get("baseline_checks"))
    if (
        verification.get("verification_receipt_id")
        != calculate_verification_receipt_id(verification)
        or verification.get("status") != "PASS"
    ):
        raise ValueError("PORTABLE_VERIFICATION_ID_OR_STATE_INVALID")
    if any(
        verification.get(key) != preflight.get(key)
        for key in ("preflight_receipt_id", "acceptance_id", "rule_bundle_digest")
    ):
        raise ValueError("PORTABLE_PREFLIGHT_BINDING_DIFFERS")
    if wiki.get("wiki_sync_receipt_id") != calculate_wiki_sync_receipt_id(wiki):
        raise ValueError("PORTABLE_WIKI_ID_DIFFERS")
    if wiki.get("wiki_sync_receipt_id") != verification.get("wiki_sync_receipt_id") or any(
        wiki.get(key) != preflight.get(key) for key in ("preflight_receipt_id", "acceptance_id")
    ):
        raise ValueError("PORTABLE_WIKI_BINDING_DIFFERS")
    _date(verification.get("verified_at"))
    if value["proof_scope"] == "HISTORICAL_SOURCE_RECEIPT":
        if manifest != _legacy_manifest(manifest) or value["run_evidence"] is not None:
            raise ValueError("PORTABLE_HISTORICAL_CLAIM_INVALID")
    elif value["proof_scope"] in {"LOCAL_WORKFLOW_VERIFICATION", "LOCAL_TOOLING_VERIFICATION"}:
        validate_source_manifest(manifest)
        if (
            verification.get("source_manifest_digest") != manifest["manifest_digest"]
            or verification.get("source_manifest_policy") != SOURCE_POLICY
        ):
            raise ValueError("PORTABLE_SOURCE_MANIFEST_BINDING_DIFFERS")
        run = _object(value["run_evidence"])
        if "selection" not in run and any(
            entry["path"] == "scripts/verification_profiles.py" and entry["sha256"]
            for entry in manifest["files"]
        ):
            raise ValueError("PORTABLE_PROFILE_METADATA_REQUIRED")
        if (
            set(run) not in (
                {"kind", "command", "exit_code", "started_at", "finished_at"},
                {"kind", "command", "exit_code", "started_at", "finished_at", "runtime"},
                {"kind", "command", "exit_code", "started_at", "finished_at", "runtime", "run_id"},
                {"kind", "command", "exit_code", "started_at", "finished_at", "runtime", "run_id", "selection", "observations"},
            )
            or ("selection" not in run and (run["kind"] != "FULL_VERIFY_SUBPROCESS"
                or run["command"] != ["Makefile.ps1", "verify"]
                or value["proof_scope"] != "LOCAL_WORKFLOW_VERIFICATION"
                or "verification_profile" in verification))
            or type(run["exit_code"]) is not int
            or run["exit_code"] != 0
        ):
            raise ValueError("PORTABLE_FULL_VERIFY_EVIDENCE_INVALID")
        if "selection" in run:
            validate_profile_run(value)
        if "runtime" in run:
            runtime = _object(run["runtime"])
            runtime_fields = {"python_version", "platform", "shell"}
            if "selection" in run:
                runtime_fields |= {"pytest_version", "python_implementation", "pytest_autoload"}
            if set(runtime) != runtime_fields or any(
                not isinstance(item, str) or not item or len(item) > 160
                for item in runtime.values()
            ):
                raise ValueError("PORTABLE_VERIFICATION_RUNTIME_INVALID")
        if "run_id" in run and (not isinstance(run["run_id"], str) or re.fullmatch(r"vr-[0-9a-f]{32}", run["run_id"]) is None):
            raise ValueError("PORTABLE_VERIFICATION_RUN_ID_INVALID")
        if (
            not _date(run["started_at"])
            <= _date(run["finished_at"])
            <= _date(verification["verified_at"])
        ):
            raise ValueError("PORTABLE_RUN_TIME_ORDER_INVALID")
    else:
        raise ValueError("PORTABLE_UNSUPPORTED_PROOF_SCOPE")
    if manifest.get("repository_digest") != verification.get("repository_digest"):
        raise ValueError("PORTABLE_TESTED_SOURCE_DIFFERS")
    entries = {item["path"]: item for item in manifest["files"]}
    updated = wiki.get("updated_paths")
    if not isinstance(updated, list):
        raise ValueError("PORTABLE_WIKI_PATHS_INVALID")
    if wiki.get("state") == "UPDATED":
        if not updated or wiki.get("no_change_reason") is not None:
            raise ValueError("PORTABLE_WIKI_UPDATED_INVALID")
    elif wiki.get("state") == "NO_CHANGE":
        if (
            updated
            or not isinstance(wiki.get("no_change_reason"), str)
            or not wiki["no_change_reason"].strip()
        ):
            raise ValueError("PORTABLE_WIKI_NO_CHANGE_INVALID")
    else:
        raise ValueError("PORTABLE_WIKI_STATE_INVALID")
    seen = set()
    for item in updated:
        entry = _object(item)
        name = _relative(entry.get("path"))
        if (
            name in seen
            or name not in entries
            or entry.get("sha256") != entries[name].get("sha256")
        ):
            raise ValueError("PORTABLE_WIKI_SOURCE_DIFFERS")
        if "size" in entries[name] and entry.get("size") != entries[name]["size"]:
            raise ValueError("PORTABLE_WIKI_SIZE_DIFFERS")
        seen.add(name)


def _read(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise ValueError("PORTABLE_SYMLINK_DATA")

    def unique_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("PORTABLE_DUPLICATE_JSON_FIELD")
            result[key] = value
        return result

    return _object(json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_fields))


def _safe_store(root: Path) -> Path:
    path = root
    for part in STORE.parts:
        path /= part
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError("PORTABLE_REPARSE_DIRECTORY")
    for name in ("bundles", "indices"):
        child = path / name
        if child.is_symlink() or (hasattr(child, "is_junction") and child.is_junction()):
            raise ValueError("PORTABLE_REPARSE_DIRECTORY")
    return path


def read_bundle(root: Path, identifier: str, *, _profile_depth: int = 0) -> dict[str, Any]:
    value = _read(_safe_store(root) / "bundles" / (_sha(identifier) + ".json"))
    validate_bundle(value)
    if value["bundle_id"] != identifier:
        raise ValueError("PORTABLE_BUNDLE_FILENAME_DIFFERS")
    from scripts.verification_profile_contract import validate_profile_baseline

    validate_profile_baseline(root, value, _profile_depth)
    return value


def _validate_index_record(root: Path, value: dict[str, Any]) -> None:
    fields = {
        "schema_version",
        "generation",
        "previous_index_id",
        "bundle_id",
        "verification_receipt_id",
        "created_at",
        "index_id",
    }
    if (
        set(value) != fields
        or value["schema_version"] != "1.0.0"
        or type(value["generation"]) is not int
        or value["generation"] < 1
    ):
        raise ValueError("PORTABLE_INDEX_SCHEMA_INVALID")
    if _sha(value["index_id"]) != digest(
        {key: item for key, item in value.items() if key != "index_id"}
    ):
        raise ValueError("PORTABLE_INDEX_ID_DIFFERS")
    _date(value["created_at"])
    bundle = read_bundle(root, value["bundle_id"])
    if value["verification_receipt_id"] != bundle["verification"]["verification_receipt_id"]:
        raise ValueError("PORTABLE_INDEX_BUNDLE_DIFFERS")
    if bundle["proof_scope"] not in {"LOCAL_WORKFLOW_VERIFICATION", "LOCAL_TOOLING_VERIFICATION"}:
        raise ValueError("PORTABLE_HISTORICAL_INDEX_INVALID")


def validate_index(root: Path, value: dict[str, Any]) -> None:
    seen: set[str] = set()
    current = value
    for _ in range(10000):
        _validate_index_record(root, current)
        identifier = current["index_id"]
        if identifier in seen:
            raise ValueError("PORTABLE_INDEX_CYCLE")
        seen.add(identifier)
        previous = current["previous_index_id"]
        if previous is None:
            if current["generation"] != 1:
                raise ValueError("PORTABLE_INDEX_PARENT_MISSING")
            return
        parent = _read(_safe_store(root) / "indices" / (_sha(previous) + ".json"))
        if (
            parent.get("index_id") != previous
            or type(parent.get("generation")) is not int
            or current["generation"] != parent["generation"] + 1
        ):
            raise ValueError("PORTABLE_INDEX_PARENT_DIFFERS")
        if _date(current["created_at"]) < _date(parent.get("created_at")):
            raise ValueError("PORTABLE_INDEX_TIME_ORDER_INVALID")
        current = parent
    raise ValueError("PORTABLE_INDEX_HISTORY_BUDGET")


def current_index(root: Path) -> dict[str, Any] | None:
    path = _safe_store(root) / "current.json"
    if not path.exists():
        return None
    current = _read(path)
    validate_index(root, current)
    archived = _read(_safe_store(root) / "indices" / (current["index_id"] + ".json"))
    if current != archived:
        raise ValueError("PORTABLE_CURRENT_ARCHIVE_DIFFERS")
    return current


def _create_only(root: Path, path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    staging = root / ".thoth/architecture"
    staging.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix="portable-", suffix=".tmp", dir=staging)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(name, path)
        except FileExistsError:
            if _read(path) != value:
                raise ValueError("PORTABLE_IMMUTABLE_RECORD_CONFLICT") from None
    finally:
        Path(name).unlink(missing_ok=True)


@contextmanager
def _publication_lock(root: Path) -> Iterator[None]:
    path = root / ".thoth/architecture/portable-publish.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if stream.seek(0, 2) == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError("PORTABLE_PUBLICATION_BUSY") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def publish_bundle(
    root: Path,
    bundle: dict[str, Any],
    *,
    expected_index_id: str | None,
    historical_only: bool = False,
    fault: Callable[[str], None] | None = None,
) -> dict[str, Any] | None:
    validate_bundle(bundle)
    from scripts.verification_profile_contract import validate_profile_baseline

    validate_profile_baseline(root, bundle)
    store = _safe_store(root)
    with _publication_lock(root):
        current = current_index(root)
        actual = None if current is None else current["index_id"]
        if actual != expected_index_id:
            raise ValueError("PORTABLE_CURRENT_INDEX_CONFLICT")
        _create_only(root, store / "bundles" / (bundle["bundle_id"] + ".json"), bundle)
        if fault:
            fault("after_bundle")
        if historical_only:
            return current
        if bundle["proof_scope"] not in {"LOCAL_WORKFLOW_VERIFICATION", "LOCAL_TOOLING_VERIFICATION"} or not source_matches(
            root, bundle["source_manifest"]
        ):
            raise ValueError("PORTABLE_CURRENT_SOURCE_DIFFERS")
        draft = {
            "schema_version": "1.0.0",
            "generation": 1 if current is None else current["generation"] + 1,
            "previous_index_id": actual,
            "bundle_id": bundle["bundle_id"],
            "verification_receipt_id": bundle["verification"]["verification_receipt_id"],
            "created_at": datetime.now(UTC).isoformat(),
        }
        index = {**draft, "index_id": digest(draft)}
        _create_only(root, store / "indices" / (index["index_id"] + ".json"), index)
        if fault:
            fault("after_index")
        validate_index(root, index)
        if not source_matches(root, bundle["source_manifest"]):
            raise ValueError("PORTABLE_CURRENT_SOURCE_DIFFERS")
        latest = current_index(root)
        if (None if latest is None else latest["index_id"]) != actual:
            raise ValueError("PORTABLE_CURRENT_INDEX_CONFLICT")
        temporary = root / ".thoth/architecture/portable-current.tmp"
        temporary.write_text(
            json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(temporary, store / "current.json")
        return index


def portable_store_digest(root: Path) -> str:
    """Validate even orphan data; exclusion from tested source is never proof validation."""
    store = _safe_store(root)
    entries: list[dict[str, str]] = []
    if not store.exists():
        return digest(entries)
    for path in sorted(store.rglob("*")):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError("PORTABLE_REPARSE_DATA")
        if path.is_dir():
            if path.relative_to(store).as_posix() not in {"bundles", "indices"}:
                raise ValueError("PORTABLE_UNEXPECTED_DIRECTORY")
            continue
        name = path.relative_to(store).as_posix()
        if name == "current.json":
            current_index(root)
        elif re.fullmatch(r"bundles/[0-9a-f]{64}\.json", name):
            read_bundle(root, path.stem)
        elif re.fullmatch(r"indices/[0-9a-f]{64}\.json", name):
            value = _read(path)
            validate_index(root, value)
            if value["index_id"] != path.stem:
                raise ValueError("PORTABLE_INDEX_FILENAME_DIFFERS")
        else:
            raise ValueError("PORTABLE_UNEXPECTED_DATA_PATH")
        entries.append({"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return digest(entries)


def require_portable_receipt(
    root: Path, verification: dict[str, Any], *, allow_tooling: bool = False,
) -> None:
    if receipt_profile(verification) == "TOOLING" and not allow_tooling:
        raise ValueError("PORTABLE_FULL_SUITE_REQUIRED")
    fields = {"source_manifest_digest", "source_manifest_policy"}
    if not fields.intersection(verification):
        return  # Immutable legacy receipts remain historical; no retroactive new proof.
    if not fields <= verification.keys():
        raise ValueError("PORTABLE_SOURCE_FIELDS_INCOMPLETE")
    portable_store_digest(root)
    current = current_index(root)
    if (
        current is None
        or current["verification_receipt_id"] != verification["verification_receipt_id"]
    ):
        raise ValueError("PORTABLE_CURRENT_VERIFICATION_DIFFERS")
    bundle = read_bundle(root, current["bundle_id"])
    if bundle["verification"] != verification or not source_matches(
        root, bundle["source_manifest"]
    ):
        raise ValueError("PORTABLE_CURRENT_SOURCE_OR_RECEIPT_DIFFERS")
