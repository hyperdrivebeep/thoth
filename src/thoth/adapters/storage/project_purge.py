"""Permanent removal of one project; the table list comes from the schema, not from a hand list.

Every table that carries a project_id column is cleared by that column. Tables without one are
either children reached through their parent row, workspace-wide records that outlive any project,
or the deletion record itself. A table that fits none of these makes the purge refuse to run, so a
new table can never be left behind by accident.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import Connection, Engine, Table, delete, func, insert, select, update

from thoth.adapters.storage import embedding_schema, operation_owner_schema, resource_scope_schema
from thoth.adapters.storage.project_deletion_schema import project_deletions
from thoth.adapters.storage.schema import metadata
from thoth.adapters.storage.transaction import read_connection, write_connection
from thoth.domain.project_deletion import ProjectDeletionRecord
from thoth.domain.upload_scope import project_upload_prefix
from thoth.ports.project_purge import ProjectPurgeBlocked, ProjectPurgePort, PurgeResult

# Importing these modules registers their tables on the shared metadata.
_SCHEMA_MODULES = (embedding_schema, operation_owner_schema, resource_scope_schema)

_ACTIVE_STATES = ("PENDING", "RUNNING")


@dataclass(frozen=True)
class ChildLink:
    """A table without project_id whose rows hang off a parent row of the project."""

    child: str
    child_column: str
    parent: str
    parent_column: str


CHILD_LINKS: tuple[ChildLink, ...] = (
    ChildLink("artifact_versions", "artifact_id", "artifacts", "artifact_id"),
    ChildLink("checkpoints", "operation_id", "operations", "operation_id"),
    ChildLink("operation_owners", "operation_id", "operations", "operation_id"),
    ChildLink("revision_parents", "revision_id", "semantic_revisions", "revision_id"),
)

# Not owned by one project: the workspace keeps them when any project is deleted.
# tui_sessions has no project_id; it only remembers which project a terminal session looked at.
WORKSPACE_TABLES = frozenset(
    {
        "organizations",
        "criterion_profiles",
        "object_profiles",
        "outcome_profiles",
        "tui_sessions",
    }
)

# Full-text index of the project's structure, outside the schema metadata. It carries project_id.
FULL_TEXT_TABLE = "structural_fts"
_NOT_PROJECT_DATA = frozenset({"alembic_version", "sqlite_sequence"})


def project_scoped_tables() -> tuple[Table, ...]:
    """Tables cleared by project_id; the project row itself comes last."""
    tables = [
        table
        for table in metadata.sorted_tables
        if "project_id" in table.c and table.name != project_deletions.name
    ]
    tables.sort(key=lambda table: table.name == "projects")
    return tuple(tables)


def unclassified_tables(names: set[str]) -> tuple[str, ...]:
    """Table names that are neither cleared, child-linked, kept, nor the deletion record."""
    known = (
        {table.name for table in project_scoped_tables()}
        | {link.child for link in CHILD_LINKS}
        | WORKSPACE_TABLES
        | _NOT_PROJECT_DATA
        | {project_deletions.name, FULL_TEXT_TABLE}
    )
    return tuple(
        sorted(
            name
            for name in names
            if name not in known and not name.startswith(f"{FULL_TEXT_TABLE}_")
        )
    )


class SqliteProjectPurge(ProjectPurgePort):
    def __init__(
        self,
        engine: Engine,
        workspace: Path,
        *,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        self._engine = engine
        self._workspace = workspace.resolve()
        self._fault_injector = fault_injector

    def _fault(self, point: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(point)

    def read_record(self, project_id: str) -> ProjectDeletionRecord | None:
        with read_connection(self._engine) as connection:
            row = (
                connection.execute(
                    select(project_deletions).where(project_deletions.c.project_id == project_id)
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        return ProjectDeletionRecord(
            project_id=str(row["project_id"]),
            name_sha256=str(row["name_sha256"]),
            deleted_at=datetime.fromisoformat(str(row["deleted_at"])),
            cleanup_pending=tuple(str(item) for item in json.loads(str(row["cleanup_json"]))),
        )

    def purge(self, record: ProjectDeletionRecord) -> PurgeResult:
        project_id = record.project_id
        with write_connection(self._engine) as connection:
            self._require_known_tables(connection)
            self._require_not_blocked(connection, project_id)
            deleted = self._delete_rows(connection, project_id)
            stored = record.model_copy(update={"cleanup_pending": self._pending_files(project_id)})
            connection.execute(
                insert(project_deletions).values(
                    project_id=stored.project_id,
                    name_sha256=stored.name_sha256,
                    deleted_at=stored.deleted_at.isoformat(),
                    cleanup_json=json.dumps(list(stored.cleanup_pending)),
                )
            )
            self._fault("purge:record")
        return PurgeResult(record=stored, deleted_rows=deleted)

    def clean_files(self, project_id: str) -> tuple[str, ...]:
        record = self.read_record(project_id)
        if record is None or not record.cleanup_pending:
            return ()
        allowed = set(self._pending_names(project_id))
        remaining: list[str] = []
        for relative in record.cleanup_pending:
            if relative not in allowed or not self._remove(self._workspace / relative):
                remaining.append(relative)
        if tuple(remaining) != record.cleanup_pending:
            with write_connection(self._engine) as connection:
                connection.execute(
                    update(project_deletions)
                    .where(project_deletions.c.project_id == project_id)
                    .values(cleanup_json=json.dumps(remaining))
                )
        return tuple(remaining)

    def _require_known_tables(self, connection: Connection) -> None:
        names = {
            str(name)
            for name in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).scalars()
        }
        stale = unclassified_tables(names)
        if stale:
            raise ProjectPurgeBlocked("PROJECT_PURGE_TABLE_UNCLASSIFIED:" + ",".join(stale))

    def _require_not_blocked(self, connection: Connection, project_id: str) -> None:
        references = metadata.tables["project_references"]
        referencing = sorted(
            str(value)
            for value in connection.execute(
                select(references.c.project_id)
                .where(
                    references.c.origin_project_id == project_id,
                    references.c.project_id != project_id,
                )
                .distinct()
            ).scalars()
        )
        if referencing:
            raise ProjectPurgeBlocked(
                "PROJECT_REFERENCED_BY_OTHER_PROJECTS", project_ids=tuple(referencing)
            )
        operations = metadata.tables["operations"]
        running = connection.execute(
            select(func.count())
            .select_from(operations)
            .where(operations.c.project_id == project_id, operations.c.state.in_(_ACTIVE_STATES))
        ).scalar_one()
        if running:
            raise ProjectPurgeBlocked("PROJECT_HAS_ACTIVE_OPERATION")
        jobs = embedding_schema.embedding_jobs
        working = connection.execute(
            select(func.count())
            .select_from(jobs)
            .where(jobs.c.project_id == project_id, jobs.c.state == "RUNNING")
        ).scalar_one()
        if working:
            raise ProjectPurgeBlocked("PROJECT_EMBEDDING_JOB_RUNNING")

    def _delete_rows(self, connection: Connection, project_id: str) -> int:
        deleted = 0
        for link in CHILD_LINKS:
            child = metadata.tables[link.child]
            parent = metadata.tables[link.parent]
            owned = select(parent.c[link.parent_column]).where(parent.c.project_id == project_id)
            deleted += connection.execute(
                delete(child).where(child.c[link.child_column].in_(owned))
            ).rowcount
            self._fault(f"purge:{link.child}")
        for table in project_scoped_tables():
            deleted += connection.execute(
                delete(table).where(table.c.project_id == project_id)
            ).rowcount
            self._fault(f"purge:{table.name}")
        deleted += connection.exec_driver_sql(
            f"DELETE FROM {FULL_TEXT_TABLE} WHERE project_id = ?", (project_id,)
        ).rowcount
        self._fault(f"purge:{FULL_TEXT_TABLE}")
        return deleted

    def _pending_names(self, project_id: str) -> tuple[str, ...]:
        return ("inbox/" + project_upload_prefix(project_id),)

    def _pending_files(self, project_id: str) -> tuple[str, ...]:
        return tuple(
            relative
            for relative in self._pending_names(project_id)
            if os.path.lexists(self._workspace / relative)
        )

    def _remove(self, path: Path) -> bool:
        """Remove a project-only folder; links are never followed, so a link stays pending."""
        try:
            if not os.path.lexists(path):
                return True
            if path.is_symlink() or path.is_junction():
                return False
            if not path.resolve().is_relative_to(self._workspace / "inbox"):
                return False
            shutil.rmtree(path)
        except OSError:
            return False
        return True
