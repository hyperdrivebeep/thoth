"""Session workspace snapshot helpers. Container disk is not the source of truth."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import tarfile
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, cast

from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.deployment_mode import STALE_RUNNING_MESSAGE, STALE_RUNNING_REASON
from thoth.domain.resource_scope import OperationResourceBinding, OperationResourceBindingBody
from thoth.protocol.jsonrpc import RpcErrorCode

LIVE_MARKER_NAME = ".hosted-review-live"
MAX_RESTORE_BYTES = 512 * 1024 * 1024
MAX_RESTORE_MEMBERS = 10_000
_DIGEST = re.compile(r"[0-9a-f]{64}")
_SKIP_NAMES = frozenset(
    {
        "secrets.json",
        ".env",
        "thoth.sqlite3-wal",
        "thoth.sqlite3-shm",
        "thoth.sqlite3-journal",
        ".hosted-review-backup.sqlite3",
    }
)
_SKIP_DIR_NAMES = frozenset({".thoth", ".codex", ".omo", "model-registry", "tmp"})


def live_marker_path(workspace: Path) -> Path:
    return workspace.resolve() / LIVE_MARKER_NAME


def workspace_is_live(workspace: Path) -> bool:
    return live_marker_path(workspace).is_file()


def mark_workspace_live(workspace: Path) -> None:
    marker = live_marker_path(workspace)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("live\n", encoding="utf-8")


def sqlite_path(workspace: Path) -> Path:
    return workspace / "db" / "thoth.sqlite3"


def object_store_root(workspace: Path) -> Path:
    return workspace / "objects" / "sha256"


_QUEUED_OPERATION_EXCLUSION = """
AND NOT EXISTS (
    SELECT 1 FROM control_records AS queue
    WHERE queue.project_id = operations.project_id
      AND queue.namespace = 'RESEARCH_EXECUTION'
      AND queue.record_type = 'QueuedResearchInput'
      AND queue.record_id = 'queue:' || operations.operation_id
      AND queue.state IN ('QUEUED', 'HOLD')
      AND queue.version = (
          SELECT MAX(latest.version) FROM control_records AS latest
          WHERE latest.project_id = queue.project_id
            AND latest.namespace = queue.namespace
            AND latest.record_id = queue.record_id
      )
)
"""


def _queue_exclusion(connection: sqlite3.Connection) -> str:
    present = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'control_records'"
    ).fetchone()
    return _QUEUED_OPERATION_EXCLUSION if present is not None else ""


def _has_table(connection: sqlite3.Connection, name: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        ).fetchone()
        is not None
    )


def sqlite_has_running(database: Path) -> bool:
    if not database.is_file():
        return False
    try:
        with closing(sqlite3.connect(database)) as connection:
            row = connection.execute(
                "SELECT 1 FROM operations WHERE state IN ('PENDING', 'RUNNING') "
                + _queue_exclusion(connection)
                + " LIMIT 1"
            ).fetchone()
    except sqlite3.Error:
        return False
    return row is not None


def referenced_object_digests(database: Path) -> tuple[str, ...]:
    if not database.is_file():
        return ()
    found: set[str] = set()
    try:
        with closing(sqlite3.connect(database)) as connection:
            tables = {
                str(row[0])
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            source_tables = (
                ("artifacts", "byte_sha256"),
                ("artifact_versions", "byte_sha256"),
            )
            for table, column in source_tables:
                if table not in tables:
                    continue
                for (raw,) in connection.execute(f"SELECT DISTINCT {column} FROM {table}"):
                    digest = str(raw or "").strip().lower()
                    if _DIGEST.fullmatch(digest):
                        found.add(digest)
    except sqlite3.Error as exc:
        raise ValueError("HOSTED_REVIEW_SNAPSHOT_DB_UNREADABLE") from exc
    return tuple(sorted(found))


def object_file_for(workspace: Path, digest: str) -> Path:
    normalized = digest.lower()
    if _DIGEST.fullmatch(normalized) is None:
        raise ValueError("HOSTED_REVIEW_SNAPSHOT_OBJECT_DIGEST_INVALID")
    return object_store_root(workspace) / normalized[:2] / normalized


def stored_object_digests(workspace: Path) -> tuple[str, ...]:
    root = object_store_root(workspace)
    if not root.is_dir():
        return ()
    found: set[str] = set()
    for path in root.rglob("*"):
        if not path.is_file() or _should_skip(path, workspace.resolve()):
            continue
        digest = path.name.lower()
        if not _DIGEST.fullmatch(digest):
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != digest:
            raise ValueError("HOSTED_REVIEW_SNAPSHOT_OBJECT_CORRUPT")
        found.add(digest)
    return tuple(sorted(found))


def snapshot_file_count(workspace: Path) -> int:
    workspace = workspace.resolve()
    count = 0
    if sqlite_path(workspace).is_file():
        count += 1
    if (workspace / "workspace-setup.json").is_file():
        count += 1
    for folder in (workspace / "inbox", object_store_root(workspace)):
        if not folder.is_dir():
            continue
        count += sum(
            1
            for path in folder.rglob("*")
            if path.is_file() and not _should_skip(path, workspace)
        )
    return count


def backup_sqlite(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    if not source.is_file():
        return
    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(destination)) as dest:
        src.backup(dest)


def fail_stale_running_sqlite(database: Path, *, completed_at: str | None = None) -> int:
    if not database.is_file():
        return 0
    stamp = completed_at or datetime.now(UTC).isoformat()
    error_payload = {
        "code": int(RpcErrorCode.DOMAIN_REJECTED),
        "message": STALE_RUNNING_MESSAGE,
        "data": {
            "reason_code": STALE_RUNNING_REASON,
            "remote_observation": "UNKNOWN",
        },
    }
    error = json.dumps(error_payload, separators=(",", ":"), sort_keys=True)
    try:
        with closing(sqlite3.connect(database)) as connection:
            if not _has_table(connection, "operation_resource_bindings"):
                # Runtime migrations must create the binding table before sealing.
                return 0
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT operation_id, project_id, scope_digest FROM operations "
                "WHERE state IN ('PENDING', 'RUNNING') " + _queue_exclusion(connection)
            ).fetchall()
            changed = 0
            for operation_id, project_id, scope_digest in rows:
                if connection.execute(
                    "SELECT 1 FROM operation_resource_bindings WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone() is not None:
                    raise ValueError("HOSTED_REVIEW_RUNNING_OPERATION_ALREADY_BOUND")
                updated = connection.execute(
                    "UPDATE operations SET state = ?, error_json = ?, result_json = NULL, "
                    "completed_at = ? WHERE operation_id = ? AND state IN ('PENDING', 'RUNNING')",
                    ("FAILED", error, stamp, operation_id),
                ).rowcount
                if updated != 1:
                    raise ValueError("HOSTED_REVIEW_STALE_OPERATION_CHANGED")
                body = OperationResourceBindingBody(
                    operation_id=str(operation_id),
                    project_id=str(project_id),
                    request_digest=str(scope_digest),
                    output_kind="ERROR",
                    output_digest=domain_digest(
                        "OPERATION_RESOURCE_OUTPUT",
                        "1.0.0",
                        canonical_payload(error_payload),
                    ),
                    resource_uses=(),
                ).model_dump(mode="python")
                binding = OperationResourceBinding.model_validate(
                    {
                        **body,
                        "binding_digest": domain_digest(
                            "OPERATION_RESOURCE_BINDING", "1.0.0", canonical_payload(body)
                        ),
                    }
                )
                connection.execute(
                    "INSERT INTO operation_resource_bindings "
                    "(operation_id, project_id, binding_digest, content_json) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        operation_id,
                        project_id,
                        binding.binding_digest,
                        binding.model_dump_json(),
                    ),
                )
                changed += 1
            connection.commit()
            return changed
    except sqlite3.Error:
        return 0


def _should_skip(path: Path, workspace: Path) -> bool:
    if path.name in _SKIP_NAMES:
        return True
    relative = path.relative_to(workspace)
    return any(part in _SKIP_DIR_NAMES for part in relative.parts)


def _require_objects(workspace: Path, referenced: tuple[str, ...], stored: tuple[str, ...]) -> None:
    missing = [digest for digest in referenced if digest not in stored]
    if missing:
        raise ValueError("HOSTED_REVIEW_SNAPSHOT_OBJECT_MISSING")


def export_workspace_archive(workspace: Path) -> bytes:
    workspace = workspace.resolve()
    buffer = io.BytesIO()
    sqlite_digest = ""
    file_count = 0
    live_db = sqlite_path(workspace)
    referenced = referenced_object_digests(live_db)
    stored = stored_object_digests(workspace)
    _require_objects(workspace, referenced, stored)
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        if live_db.is_file():
            backup = workspace / "db" / ".hosted-review-backup.sqlite3"
            backup_sqlite(live_db, backup)
            sqlite_digest = hashlib.sha256(backup.read_bytes()).hexdigest()
            archive.add(backup, arcname="db/thoth.sqlite3")
            file_count += 1
            backup.unlink(missing_ok=True)
        setup = workspace / "workspace-setup.json"
        if setup.is_file():
            archive.add(setup, arcname="workspace-setup.json")
            file_count += 1
        for folder in (workspace / "inbox", object_store_root(workspace)):
            if not folder.is_dir():
                continue
            for path in sorted(folder.rglob("*")):
                if path.is_dir() or _should_skip(path, workspace):
                    continue
                archive.add(path, arcname=path.relative_to(workspace).as_posix())
                file_count += 1
        manifest = json.dumps(
            {
                "created_at": datetime.now(UTC).isoformat(),
                "session_id": os.environ.get("THOTH_REVIEW_SESSION_ID", "").strip(),
                "sqlite_digest": sqlite_digest,
                "file_count": file_count,
                "research_running": sqlite_has_running(live_db),
                "canonical": "r2-snapshot",
                "includes_object_store": True,
                "referenced_object_digests": list(referenced),
                "object_digests": list(stored),
                "objects_complete": True,
            },
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        info = tarfile.TarInfo("latest.json")
        info.size = len(manifest)
        archive.addfile(info, io.BytesIO(manifest))
    return buffer.getvalue()


def _restore_archive_into_directory(
    workspace: Path, payload: bytes | BinaryIO
) -> dict[str, object]:
    raw = payload if isinstance(payload, bytes) else payload.read()
    buffer = io.BytesIO(raw)
    restored = 0
    restored_bytes = 0
    with tarfile.open(fileobj=buffer, mode="r:gz") as archive:
        members = archive.getmembers()
        if len(members) > MAX_RESTORE_MEMBERS:
            raise ValueError("HOSTED_REVIEW_SNAPSHOT_TOO_LARGE")
        for member in members:
            name = Path(member.name)
            if member.issym() or member.islnk() or name.is_absolute() or ".." in name.parts:
                raise ValueError("HOSTED_REVIEW_SNAPSHOT_PATH_REJECTED")
            if name.name in _SKIP_NAMES or any(part in _SKIP_DIR_NAMES for part in name.parts):
                continue
            size = int(member.size or 0)
            restored_bytes += size
            if restored_bytes > MAX_RESTORE_BYTES:
                raise ValueError("HOSTED_REVIEW_SNAPSHOT_TOO_LARGE")
            archive.extract(member, path=workspace, filter="data")
            restored += 1
    stored = stored_object_digests(workspace)
    manifest_path = workspace / "latest.json"
    referenced: tuple[str, ...] = ()
    if manifest_path.is_file():
        try:
            manifest_raw: object = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("HOSTED_REVIEW_SNAPSHOT_MANIFEST_INVALID") from exc
        if not isinstance(manifest_raw, dict):
            raise ValueError("HOSTED_REVIEW_SNAPSHOT_MANIFEST_INVALID")
        manifest = cast(dict[str, object], manifest_raw)
        expected_session = os.environ.get("THOTH_REVIEW_SESSION_ID", "").strip()
        recorded_session = str(manifest.get("session_id") or "").strip()
        if expected_session and recorded_session and recorded_session != expected_session:
            raise ValueError("HOSTED_REVIEW_SNAPSHOT_SESSION_MISMATCH")
        raw_refs: object = (
            manifest.get("referenced_object_digests") or manifest.get("object_digests") or []
        )
        if not isinstance(raw_refs, list):
            raise ValueError("HOSTED_REVIEW_SNAPSHOT_MANIFEST_INVALID")
        reference_values = cast(list[object], raw_refs)
        referenced = tuple(
            str(item).lower()
            for item in reference_values
            if _DIGEST.fullmatch(str(item).lower())
        )
    database = sqlite_path(workspace)
    db_refs = referenced_object_digests(database)
    _require_objects(workspace, referenced, stored)
    _require_objects(workspace, db_refs, stored)
    closed = fail_stale_running_sqlite(database)
    digest = hashlib.sha256(database.read_bytes()).hexdigest() if database.is_file() else ""
    return {
        "restored_members": restored,
        "stale_closed": closed,
        "sqlite_digest": digest,
        "object_count": len(stored),
        "objects_complete": True,
    }


def restore_workspace_archive(workspace: Path, payload: bytes | BinaryIO) -> dict[str, object]:
    workspace = workspace.resolve()
    parent = workspace.parent
    parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    staging = parent / f".{workspace.name}.restore-staging-{token}"
    rollback = parent / f".{workspace.name}.restore-rollback-{token}"
    raw = payload if isinstance(payload, bytes) else payload.read()
    try:
        staging.mkdir(parents=True)
        restored = _restore_archive_into_directory(staging, raw)
        if workspace.exists():
            workspace.replace(rollback)
        staging.replace(workspace)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        if rollback.exists() and not workspace.exists():
            rollback.replace(workspace)
        raise
    if rollback.exists():
        shutil.rmtree(rollback, ignore_errors=True)
    return restored
