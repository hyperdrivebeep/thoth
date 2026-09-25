"""The forward migration preserves old policy rows and permits repeated content."""

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config


def _config(database: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    database.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("THOTH_ALEMBIC_URL", "sqlite+pysqlite:///" + database.as_posix())
    return Config("alembic.ini")


def _rows(database: Path) -> list[tuple[object, ...]]:
    with sqlite3.connect(database) as connection:
        return connection.execute(
            "SELECT policy_id,project_id,version,payload_json,policy_digest,created_at "
            "FROM project_policies ORDER BY version"
        ).fetchall()


def _insert(database: Path, *, policy_id: str, version: int, digest: str) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO project_policies "
            "(policy_id,project_id,version,payload_json,policy_digest,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (
                policy_id,
                "project:history",
                version,
                '{"external_write":false}',
                digest,
                "2026-09-01T00:00:00+00:00",
            ),
        )


def _seed_related_history(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO governance_revisions VALUES (?,?,?,?,?,?,?,?)",
            (
                "project:history", "PROJECT", "project:history", 1,
                "c" * 64, '{"preserve":"revision bytes"}', "d" * 64,
                '{"preserve":"receipt bytes"}',
            ),
        )
        connection.execute(
            "INSERT INTO governance_heads VALUES (?,?,?,?,?)",
            ("project:history", "PROJECT", "project:history", 1, "c" * 64),
        )
        connection.execute(
            "INSERT INTO working_heads VALUES (?,?,?,?)",
            ("project:history", "POLICY", "f" * 64, "2026-09-01T00:00:00+00:00"),
        )
        connection.execute(
            "INSERT INTO receipts VALUES (?,?,?,?,?,?,?)",
            (
                "receipt:history", "project:history", "TRANSITION",
                '{"preserve":"original receipt bytes"}', "e" * 64, None,
                "2026-09-01T00:00:00+00:00",
            ),
        )


def _related_history(database: Path) -> dict[str, list[tuple[object, ...]]]:
    with sqlite3.connect(database) as connection:
        return {
            table: connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            for table in (
                "governance_revisions", "governance_heads", "working_heads", "receipts"
            )
        }


def test_forward_migration_preserves_rows_and_repeated_digest_downgrade_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "history.sqlite3"
    config = _config(database, monkeypatch)
    command.upgrade(config, "2af5d3e76c81")
    _insert(database, policy_id="policy:first", version=1, digest="a" * 64)
    _seed_related_history(database)
    original = _rows(database)
    related_before = _related_history(database)

    command.upgrade(config, "head")
    assert _rows(database) == original
    assert _related_history(database) == related_before
    _insert(database, policy_id="policy:second", version=2, digest="a" * 64)
    assert [row[0] for row in _rows(database)] == ["policy:first", "policy:second"]
    with sqlite3.connect(database) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO project_policies "
            "(policy_id,project_id,version,payload_json,policy_digest,created_at) "
            "VALUES (?,?,?,?,?,?)",
            ("policy:duplicate-version", "project:history", 2, "{}", "b" * 64, "now"),
        )
    before_refusal = _rows(database)
    with pytest.raises(RuntimeError, match="cannot restore historical policy_digest uniqueness"):
        command.downgrade(config, "2af5d3e76c81")
    assert _rows(database) == before_refusal
    assert _related_history(database) == related_before
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "3b06e4f87d92",
        )


def test_downgrade_without_repeated_digest_restores_old_constraint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "reversible.sqlite3"
    config = _config(database, monkeypatch)
    command.upgrade(config, "head")
    _insert(database, policy_id="policy:first", version=1, digest="a" * 64)
    original = _rows(database)
    command.downgrade(config, "2af5d3e76c81")
    assert _rows(database) == original
    with sqlite3.connect(database) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO project_policies "
            "(policy_id,project_id,version,payload_json,policy_digest,created_at) "
            "VALUES (?,?,?,?,?,?)",
            ("policy:second", "project:history", 2, "{}", "a" * 64, "now"),
        )


def test_forward_migration_refuses_unrecognized_index_before_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "custom-index.sqlite3"
    config = _config(database, monkeypatch)
    command.upgrade(config, "2af5d3e76c81")
    _insert(database, policy_id="policy:first", version=1, digest="a" * 64)
    original = _rows(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE INDEX ix_custom_policy_digest ON project_policies(policy_digest)"
        )
    with pytest.raises(RuntimeError, match="expected migration source"):
        command.upgrade(config, "head")
    assert _rows(database) == original
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "2af5d3e76c81",
        )
        indexes = connection.execute('PRAGMA index_list("project_policies")').fetchall()
        assert any(row[1] == "ix_custom_policy_digest" for row in indexes)
