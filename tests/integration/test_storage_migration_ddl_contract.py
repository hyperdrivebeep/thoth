"""103-V: real Alembic DDL is stable on a new temporary database and roundtrip."""

from __future__ import annotations

import importlib
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from alembic.runtime.environment import EnvironmentContext
from sqlalchemy import URL
from tests.integration.migration_head_helpers import current_single_head

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/fixtures/storage_migration_ddl.json"


def snapshot(database: Path) -> dict[str, Any]:
    with closing(sqlite3.connect(database)) as connection:
        rows = connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name, tbl_name"
        ).fetchall()
        heads = connection.execute(
            "SELECT version_num FROM alembic_version ORDER BY version_num"
        ).fetchall()
    return {"schema_rows": [list(row) for row in rows], "heads": [row[0] for row in heads]}


def capture_roundtrip(database: Path, evidence_dir: Path | None = None) -> dict[str, Any]:
    assert not database.exists(), "only a new test database may be migrated"
    schema = importlib.import_module("thoth.adapters.storage.schema")
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    url = URL.create("sqlite+pysqlite", database=str(database.resolve())).render_as_string(
        hide_password=False
    )
    config.set_main_option("sqlalchemy.url", url)
    target_metadata_checks: list[bool] = []
    original = EnvironmentContext.configure

    def preserve(name: str, value: dict[str, Any]) -> None:
        if evidence_dir is not None:
            evidence_dir.mkdir(parents=True, exist_ok=True)
            (evidence_dir / (name + ".json")).write_text(
                json.dumps(value, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )

    def configure(self: EnvironmentContext, *args: Any, **kwargs: Any) -> Any:
        assert kwargs["target_metadata"] is schema.metadata
        target_metadata_checks.append(True)
        return original(self, *args, **kwargs)

    with (
        patch.dict(os.environ, {"THOTH_ALEMBIC_URL": url}),
        patch.object(EnvironmentContext, "configure", configure),
    ):
        command.upgrade(config, "head")
        first = snapshot(database)
        preserve("fresh", first)
        assert first["heads"] == [current_single_head()]
        command.downgrade(config, "base")
        down = snapshot(database)
        preserve("base", down)
        assert down["heads"] == []
        assert not {"entity_snapshots", "structural_fts"}.intersection(
            row[1] for row in down["schema_rows"]
        )
        command.upgrade(config, "head")
        second = snapshot(database)
        preserve("reupgraded", second)
    assert len(target_metadata_checks) == 3
    assert second == first, "HOLD: original migration DDL roundtrip differs"
    assert snapshot(database) == first  # independent sqlite3 reopen
    return {"contract": first, "target_metadata_same_object_checks": len(target_metadata_checks)}


def test_migration_ddl_roundtrip_bytes(tmp_path: Path) -> None:
    observed = capture_roundtrip(tmp_path / "schema.sqlite3", tmp_path)
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert observed == expected
    for before, after in zip(
        expected["contract"]["schema_rows"], observed["contract"]["schema_rows"], strict=True
    ):
        assert before[:3] == after[:3]
        assert (None if before[3] is None else before[3].encode("utf-8")) == (
            None if after[3] is None else after[3].encode("utf-8")
        )
