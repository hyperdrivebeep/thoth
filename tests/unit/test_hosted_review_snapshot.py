from __future__ import annotations

import hashlib
import json
import sqlite3
import tarfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from thoth.apps.hosted_review_composition import fail_stale_running_operations
from thoth.apps.hosted_review_snapshot import (
    MAX_RESTORE_BYTES,
    MAX_RESTORE_MEMBERS,
    export_workspace_archive,
    fail_stale_running_sqlite,
    mark_workspace_live,
    object_store_root,
    restore_workspace_archive,
    sqlite_has_running,
    sqlite_path,
    workspace_is_live,
)
from thoth.apps.runtime import create_runtime
from thoth.domain.enums import OperationState
from thoth.domain.resource_scope import OperationResourceBinding
from thoth.ports.control_record import ControlRecordStorePort
from thoth.ports.operation import OperationStorePort
from thoth.ports.project import ProjectStorePort


def test_runtime_stale_sweep_preserves_queued_operation() -> None:
    class Operations:
        def __init__(self) -> None:
            self.failed: list[str] = []

        def list_by_project(self, project_id: str) -> tuple[SimpleNamespace, ...]:
            assert project_id == "p"
            return (
                SimpleNamespace(
                    operation_id="active", project_id="p", state=OperationState.RUNNING
                ),
                SimpleNamespace(
                    operation_id="queued", project_id="p", state=OperationState.RUNNING
                ),
            )

        def fail(self, operation_id: str, error: object, *, completed_at: object) -> None:
            self.failed.append(operation_id)

    class Projects:
        def list(self) -> tuple[SimpleNamespace, ...]:
            return (SimpleNamespace(project_id="p"),)

    class Controls:
        def read(self, project_id: str, namespace: str, record_id: str) -> object:
            assert (project_id, namespace) == ("p", "RESEARCH_EXECUTION")
            if record_id == "queue:queued":
                return SimpleNamespace(record_type="QueuedResearchInput", state="QUEUED")
            return None

    operations = Operations()
    assert (
        fail_stale_running_operations(
            cast(OperationStorePort, operations),
            cast(ProjectStorePort, Projects()),
            controls=cast(ControlRecordStorePort, Controls()),
        )
        == 1
    )
    assert operations.failed == ["active"]


def test_stale_sweep_preserves_only_latest_queued_operations(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE operations (operation_id TEXT PRIMARY KEY, project_id TEXT, "
            "scope_digest TEXT, state TEXT, error_json TEXT, result_json TEXT, completed_at TEXT)"
        )
        connection.execute(
            "CREATE TABLE operation_resource_bindings (operation_id TEXT PRIMARY KEY, "
            "project_id TEXT, binding_digest TEXT, content_json TEXT)"
        )
        connection.execute(
            "CREATE TABLE control_records (project_id TEXT, namespace TEXT, "
            "record_type TEXT, record_id TEXT, state TEXT, version INTEGER)"
        )
        connection.executemany(
            "INSERT INTO operations (operation_id, project_id, scope_digest, state) "
            "VALUES (?, 'p', ?, 'RUNNING')",
            (("active", "a" * 64), ("queued", "b" * 64)),
        )
        connection.execute(
            "INSERT INTO control_records VALUES "
            "('p', 'RESEARCH_EXECUTION', 'QueuedResearchInput', 'queue:queued', 'QUEUED', 1)"
        )
    assert sqlite_has_running(database) is True
    assert fail_stale_running_sqlite(database) == 1
    assert sqlite_has_running(database) is False
    with sqlite3.connect(database) as connection:
        states = dict(connection.execute("SELECT operation_id, state FROM operations"))
        assert states == {"active": "FAILED", "queued": "RUNNING"}
        bound = connection.execute(
            "SELECT content_json FROM operation_resource_bindings WHERE operation_id = 'active'"
        ).fetchone()
        assert bound is not None
        binding = OperationResourceBinding.model_validate_json(bound[0])
        assert binding.resource_uses == ()
        connection.execute(
            "INSERT INTO control_records VALUES "
            "('p', 'RESEARCH_EXECUTION', 'QueuedResearchInput', 'queue:queued', 'ACTIVE', 2)"
        )
    assert sqlite_has_running(database) is True
    assert fail_stale_running_sqlite(database) == 1
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT state FROM operations WHERE operation_id = 'queued'"
        ).fetchone() == ("FAILED",)


def _write_object(workspace: Path, payload: bytes) -> str:
    digest = hashlib.sha256(payload).hexdigest()
    path = object_store_root(workspace) / digest[:2] / digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return digest


def _write_artifact_db(workspace: Path, digest: str) -> None:
    database = sqlite_path(workspace)
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE artifacts (byte_sha256 TEXT)")
        connection.execute("INSERT INTO artifacts (byte_sha256) VALUES (?)", (digest,))
        connection.commit()


def test_snapshot_roundtrip_uses_backup_not_stale_disk(tmp_path: Path) -> None:
    source = tmp_path / "live"
    runtime = create_runtime(source)
    runtime.close()
    inbox = source / "inbox" / "keep"
    inbox.mkdir(parents=True)
    (inbox / "note.txt").write_text("canonical-inbox", encoding="utf-8")
    (source / "workspace-setup.json").write_text(
        '{"schema_version":1,"revision":1,"internet_consent":"DENIED"}',
        encoding="utf-8",
    )
    stale = source / "db" / "stale-disk.sqlite3"
    stale.write_bytes(b"not-canonical")
    archive = export_workspace_archive(source)
    target = tmp_path / "restored"
    restored = restore_workspace_archive(target, archive)
    assert restored["stale_closed"] == 0
    assert (target / "inbox" / "keep" / "note.txt").read_text(encoding="utf-8") == "canonical-inbox"
    assert sqlite_path(target).is_file()
    assert not (target / "db" / "stale-disk.sqlite3").exists()
    live_db = sqlite_path(source)
    live_db.write_bytes(live_db.read_bytes() + b"mutated-after-export")
    restored_again = tmp_path / "restored-again"
    restore_workspace_archive(restored_again, archive)
    assert sqlite_path(restored_again).read_bytes() != live_db.read_bytes()
    with tarfile.open(fileobj=BytesIO(archive), mode="r:gz") as packed:
        manifest_file = packed.extractfile("latest.json")
        assert manifest_file is not None
        manifest = json.loads(manifest_file.read().decode("utf-8"))
    assert manifest["canonical"] == "r2-snapshot"
    assert manifest["includes_object_store"] is True
    assert manifest["file_count"] >= 2
    assert manifest["research_running"] is False


def test_export_includes_object_store_and_restore_roundtrips(tmp_path: Path) -> None:
    source = tmp_path / "live"
    payload = b"canonical-object-bytes"
    digest = _write_object(source, payload)
    _write_artifact_db(source, digest)
    archive = export_workspace_archive(source)
    with tarfile.open(fileobj=BytesIO(archive), mode="r:gz") as packed:
        manifest_file = packed.extractfile("latest.json")
        assert manifest_file is not None
        manifest = json.loads(manifest_file.read().decode("utf-8"))
        names = set(packed.getnames())
    assert digest in manifest["referenced_object_digests"]
    assert digest in manifest["object_digests"]
    assert manifest["objects_complete"] is True
    assert f"objects/sha256/{digest[:2]}/{digest}" in names
    target = tmp_path / "restored"
    restored = restore_workspace_archive(target, archive)
    assert restored["objects_complete"] is True
    assert restored["object_count"] == 1
    assert (object_store_root(target) / digest[:2] / digest).read_bytes() == payload


def test_export_fails_when_referenced_object_is_missing(tmp_path: Path) -> None:
    source = tmp_path / "live"
    _write_artifact_db(source, "a" * 64)
    with pytest.raises(ValueError, match="HOSTED_REVIEW_SNAPSHOT_OBJECT_MISSING"):
        export_workspace_archive(source)


def test_export_fails_when_object_bytes_do_not_match_digest(tmp_path: Path) -> None:
    source = tmp_path / "live"
    digest = hashlib.sha256(b"expected").hexdigest()
    path = object_store_root(source) / digest[:2] / digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"corrupt")
    _write_artifact_db(source, digest)
    with pytest.raises(ValueError, match="HOSTED_REVIEW_SNAPSHOT_OBJECT_CORRUPT"):
        export_workspace_archive(source)


def test_restore_rejects_incomplete_object_store(tmp_path: Path) -> None:
    digest = "b" * 64
    database = tmp_path / "source.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE artifacts (byte_sha256 TEXT)")
        connection.execute("INSERT INTO artifacts (byte_sha256) VALUES (?)", (digest,))
        connection.commit()
    db_bytes = database.read_bytes()
    buffer = BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo("db/thoth.sqlite3")
        info.size = len(db_bytes)
        archive.addfile(info, BytesIO(db_bytes))
        manifest = json.dumps(
            {
                "referenced_object_digests": [digest],
                "object_digests": [digest],
                "objects_complete": True,
            }
        ).encode("utf-8")
        latest = tarfile.TarInfo("latest.json")
        latest.size = len(manifest)
        archive.addfile(latest, BytesIO(manifest))
    with pytest.raises(ValueError, match="HOSTED_REVIEW_SNAPSHOT_OBJECT_MISSING"):
        restore_workspace_archive(tmp_path / "out", buffer.getvalue())


def test_restore_rejects_path_escape_and_symlink(tmp_path: Path) -> None:
    escaped = BytesIO()
    with tarfile.open(fileobj=escaped, mode="w:gz") as archive:
        payload = b"nope"
        info = tarfile.TarInfo("../evil.txt")
        info.size = len(payload)
        archive.addfile(info, BytesIO(payload))
    with pytest.raises(ValueError, match="HOSTED_REVIEW_SNAPSHOT_PATH_REJECTED"):
        restore_workspace_archive(tmp_path / "escaped", escaped.getvalue())
    linked = BytesIO()
    with tarfile.open(fileobj=linked, mode="w:gz") as archive:
        info = tarfile.TarInfo("inbox/link")
        info.type = tarfile.SYMTYPE
        info.linkname = "note.txt"
        archive.addfile(info)
    with pytest.raises(ValueError, match="HOSTED_REVIEW_SNAPSHOT_PATH_REJECTED"):
        restore_workspace_archive(tmp_path / "linked", linked.getvalue())


def test_restore_rejects_session_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "live"
    source.mkdir()
    (source / "inbox").mkdir()
    (source / "inbox" / "note.txt").write_text("keep", encoding="utf-8")
    monkeypatch.setenv("THOTH_REVIEW_SESSION_ID", "session-a")
    archive = export_workspace_archive(source)
    monkeypatch.setenv("THOTH_REVIEW_SESSION_ID", "session-b")
    with pytest.raises(ValueError, match="HOSTED_REVIEW_SNAPSHOT_SESSION_MISMATCH"):
        restore_workspace_archive(tmp_path / "other", archive)


def test_restore_rejects_oversize_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "live"
    source.mkdir()
    inbox = source / "inbox"
    inbox.mkdir()
    (inbox / "one.txt").write_text("a", encoding="utf-8")
    (inbox / "two.txt").write_text("b", encoding="utf-8")
    archive = export_workspace_archive(source)
    monkeypatch.setattr("thoth.apps.hosted_review_snapshot.MAX_RESTORE_MEMBERS", 1)
    with pytest.raises(ValueError, match="HOSTED_REVIEW_SNAPSHOT_TOO_LARGE"):
        restore_workspace_archive(tmp_path / "members", archive)
    monkeypatch.setattr(
        "thoth.apps.hosted_review_snapshot.MAX_RESTORE_MEMBERS", MAX_RESTORE_MEMBERS
    )
    monkeypatch.setattr("thoth.apps.hosted_review_snapshot.MAX_RESTORE_BYTES", 1)
    with pytest.raises(ValueError, match="HOSTED_REVIEW_SNAPSHOT_TOO_LARGE"):
        restore_workspace_archive(tmp_path / "bytes", archive)
    monkeypatch.setattr("thoth.apps.hosted_review_snapshot.MAX_RESTORE_BYTES", MAX_RESTORE_BYTES)


def test_fail_stale_running_does_not_mark_success(tmp_path: Path) -> None:
    assert fail_stale_running_sqlite(tmp_path / "missing.sqlite3") == 0


def test_live_marker_is_workspace_local(tmp_path: Path) -> None:
    live = tmp_path / "live"
    other = tmp_path / "other"
    assert workspace_is_live(live) is False
    mark_workspace_live(live)
    assert workspace_is_live(live) is True
    assert workspace_is_live(other) is False
