from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import URL

from thoth.domain.migration import (
    MigrationFailure,
    MigrationFailureCode,
    MigrationResult,
)
from thoth.ports.migration import SchemaMigrationPort

_LEGACY_MARKERS: tuple[tuple[str, str], ...] = (
    ("e154fa1aa4eb", "entity_snapshots"),
    ("b9f01104c6de", "operations"),
    ("c7e8a19fe3a1", "artifacts"),
    ("d4ac821bf1e2", "memory_records"),
    ("e311a930d5f4", "threads"),
    ("f9a43b25d201", "events"),
    ("a31f6b8c4d20", "project_roles"),
    ("b42e7c9d5e31", "thread_activities"),
    ("c53f8dae6f42", "investigations"),
    ("d64a9ebf7043", "evidence_sources"),
    ("e75badc08154", "criterion_profiles"),
    ("f86cbed19265", "decision_objects"),
    ("0a97cfe2a376", "hypotheses"),
    ("1ba8d0f3b487", "action_records"),
    ("2cb9e104c598", "plan_executions"),
    ("3dca0215d6a9", "outcome_series"),
    ("4edb1326e7ba", "control_records"),
    ("5a02d4c8f1b7", "acquisition_search_intents"),
    ("6a10d4f9c2e8", "receipt_dag_nodes"),
    ("7b06e5a9d3f1", "memory_revision_ledger"),
    ("8c13f6b2a4d9", "auth_sessions"),
    ("9d12a7c5e3b8", "field_protocol_seals"),
    ("a2b14c8d6e90", "project_head_sets"),
    ("b3c25d9e7f01", "behavior_artifacts"),
    ("c4d36e8f102a", "criterion_profiles"),
    ("d5e47f9013ab", "tui_sessions"),
)


class AlembicSchemaMigrator(SchemaMigrationPort):
    def __init__(
        self,
        database_path: Path,
        *,
        repository_root: Path | None = None,
    ) -> None:
        self._database_path = database_path.resolve()
        self._root = (
            repository_root.resolve()
            if repository_root is not None
            else Path(__file__).resolve().parents[4]
        )

    def upgrade_head(self) -> MigrationResult:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        config = Config(str(self._root / "alembic.ini"))
        config.set_main_option("script_location", str(self._root / "migrations"))
        config.set_main_option(
            "sqlalchemy.url",
            URL.create(
                "sqlite+pysqlite",
                database=str(self._database_path),
            ).render_as_string(hide_password=False),
        )
        try:
            script_heads = tuple(sorted(ScriptDirectory.from_config(config).get_heads()))
        except Exception as exc:
            raise MigrationFailure(
                MigrationFailureCode.SCRIPT_HEAD_COUNT_INVALID,
                "migration script repository is missing or invalid",
            ) from exc
        if len(script_heads) != 1:
            raise MigrationFailure(
                MigrationFailureCode.SCRIPT_HEAD_COUNT_INVALID,
                f"migration script graph requires exactly one head; found {len(script_heads)}",
            )
        before = self._database_heads()
        if len(before) > 1:
            raise MigrationFailure(
                MigrationFailureCode.DATABASE_HEAD_COUNT_INVALID,
                f"database migration state requires at most one head; found {len(before)}",
            )
        adopted_legacy = False
        if not before:
            legacy_revision = self._legacy_revision()
            if legacy_revision is not None:
                try:
                    command.stamp(config, legacy_revision)
                except Exception as exc:
                    raise MigrationFailure(
                        MigrationFailureCode.UPGRADE_FAILED,
                        "legacy schema adoption failed closed",
                    ) from exc
                adopted_legacy = True
        try:
            command.upgrade(config, "head")
        except Exception as exc:
            raise MigrationFailure(
                MigrationFailureCode.UPGRADE_FAILED,
                "database migration upgrade failed closed",
            ) from exc
        after = self._database_heads()
        if after != script_heads:
            raise MigrationFailure(
                MigrationFailureCode.SCHEMA_NOT_CURRENT,
                "database migration head does not match the authoritative script head",
            )
        return MigrationResult(
            database_path=str(self._database_path),
            before_heads=before,
            after_heads=after,
            target_head=script_heads[0],
            upgraded=before != after,
            adopted_legacy_schema=adopted_legacy,
        )

    def _database_heads(self) -> tuple[str, ...]:
        if not self._database_path.is_file():
            return ()
        with sqlite3.connect(self._database_path) as connection:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='alembic_version'"
            ).fetchone()
            if exists is None:
                return ()
            rows = connection.execute("SELECT version_num FROM alembic_version").fetchall()
        return tuple(sorted(str(row[0]) for row in rows))

    def _legacy_revision(self) -> str | None:
        tables = self._database_tables()
        if not tables:
            return None
        if "alembic_version" in tables:
            return None
        required_core = {
            "entity_snapshots",
            "projects",
            "receipts",
            "semantic_revisions",
            "working_heads",
            "structural_fts",
        }
        if not required_core.issubset(tables):
            raise MigrationFailure(
                MigrationFailureCode.LEGACY_SCHEMA_UNRECOGNIZED,
                "legacy database has no migration stamp and does not match a known schema",
            )
        operation_columns = self._database_columns("operations")
        if {
            "owner_actor_id",
            "owner_session_id",
            "owner_role_assignment_id",
            "owner_data_scopes_json",
        }.issubset(operation_columns):
            return "f7a69b2345cd"
        candidates = tuple(
            revision for revision, marker in _LEGACY_MARKERS if marker in tables
        )
        if not candidates:
            raise MigrationFailure(
                MigrationFailureCode.LEGACY_SCHEMA_UNRECOGNIZED,
                "legacy database schema revision could not be inferred",
            )
        return candidates[-1]

    def _database_columns(self, table: str) -> frozenset[str]:
        if not self._database_path.is_file():
            return frozenset()
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
        return frozenset(str(row[1]) for row in rows)

    def _database_tables(self) -> frozenset[str]:
        if not self._database_path.is_file():
            return frozenset()
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            ).fetchall()
        return frozenset(str(row[0]) for row in rows)


def migrate_sqlite_database(database_path: Path) -> MigrationResult:
    return AlembicSchemaMigrator(database_path).upgrade_head()
