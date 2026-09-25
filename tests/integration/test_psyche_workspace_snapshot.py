"""SQLite WAL source data survives read-only backup and clone-only migration."""

import sqlite3
from pathlib import Path

import pytest
import tests.integration.psyche_workspace_snapshot as snapshot_module

import thoth.adapters.storage.bundle as bundle_module
from thoth.apps.storage_composition import open_stores
from thoth.domain.migration import MigrationResult


def test_read_only_backup_migrates_only_the_temporary_clone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_workspace = tmp_path / "original"
    source_db = source_workspace / "db" / "thoth.sqlite3"
    source_stores = open_stores(source_workspace)
    source_stores.close()
    original = sqlite3.connect(source_db)
    try:
        assert original.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        original.execute("CREATE TABLE fixture_marker (value TEXT NOT NULL)")
        original.execute("INSERT INTO fixture_marker (value) VALUES (?)", ("preserved",))
        original.commit()
        source_wal = Path(str(source_db) + "-wal")
        assert source_wal.is_file()
        original_main_bytes = source_db.read_bytes()
        original_wal_bytes = source_wal.read_bytes()
        schema_query = "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        original_schema = tuple(row[0] for row in original.execute(schema_query))
        original_rows = original.execute("SELECT value FROM fixture_marker").fetchall()
        assert "fixture_marker" in original_schema
        assert "alembic_version" in original_schema
        assert original_rows == [("preserved",)]

        real_connect = sqlite3.connect
        opens: list[tuple[str, bool]] = []

        def checked_connect(database: str | Path, *, uri: bool = False) -> sqlite3.Connection:
            opens.append((str(database), uri))
            return real_connect(database, uri=uri)

        destination_workspace = tmp_path / "clone"
        with monkeypatch.context() as patch:
            patch.setattr(snapshot_module.sqlite3, "connect", checked_connect)
            clone_db = snapshot_module.clone_database_read_only(
                source_workspace, destination_workspace
            )
        assert clone_db != source_db
        assert len(opens) == 2
        assert opens[0] == (source_db.resolve().as_uri() + "?mode=ro", True)
        assert opens[1] == (str(clone_db), False)

        real_migrate = bundle_module.migrate_sqlite_database
        migrated_paths: list[Path] = []

        def observed_migrate(database_path: Path) -> MigrationResult:
            migrated_paths.append(database_path)
            return real_migrate(database_path)

        with monkeypatch.context() as patch:
            patch.setattr(bundle_module, "migrate_sqlite_database", observed_migrate)
            stores = open_stores(destination_workspace)
        try:
            assert stores.projects.list() == ()
        finally:
            stores.close()
        assert migrated_paths == [clone_db]

        cloned = sqlite3.connect(clone_db)
        try:
            assert cloned.execute("SELECT value FROM fixture_marker").fetchall() == original_rows
            cloned.execute("INSERT INTO fixture_marker (value) VALUES (?)", ("clone-only",))
            cloned.commit()
            assert cloned.execute("SELECT value FROM fixture_marker").fetchall() == [
                ("preserved",),
                ("clone-only",),
            ]
            assert cloned.execute(
                "SELECT name FROM sqlite_master WHERE name='alembic_version'"
            ).fetchone() == ("alembic_version",)
        finally:
            cloned.close()

        assert source_db.read_bytes() == original_main_bytes
        assert source_wal.read_bytes() == original_wal_bytes
        assert tuple(row[0] for row in original.execute(schema_query)) == original_schema
        assert original.execute("SELECT value FROM fixture_marker").fetchall() == original_rows
    finally:
        original.close()
