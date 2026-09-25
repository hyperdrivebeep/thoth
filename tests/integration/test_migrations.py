from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from pytest import MonkeyPatch


def _tables(path: Path) -> set[str]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        ).fetchall()
    return {str(row[0]) for row in rows}


def test_migration_fresh_downgrade_upgrade_replay(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    database = tmp_path / "migration.sqlite3"
    monkeypatch.setenv(
        "THOTH_ALEMBIC_URL",
        "sqlite+pysqlite:///" + database.as_posix(),
    )
    config = Config("alembic.ini")

    command.upgrade(config, "head")
    first_tables = _tables(database)
    assert {"entity_snapshots", "semantic_revisions", "working_heads", "structural_fts"} <= (
        first_tables
    )

    command.downgrade(config, "base")
    assert "entity_snapshots" not in _tables(database)
    assert "structural_fts" not in _tables(database)

    command.upgrade(config, "head")
    assert _tables(database) == first_tables
