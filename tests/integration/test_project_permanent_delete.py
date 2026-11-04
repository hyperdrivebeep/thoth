"""project/permanentDelete through the normal bus: its reach, its refusals and its failures."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, Integer, LargeBinary, Table, insert, select, text
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import request, value

from thoth.adapters.models.catalog import StaticModelCatalog
from thoth.adapters.storage import project_purge
from thoth.adapters.storage.project_purge import (
    CHILD_LINKS,
    WORKSPACE_TABLES,
    SqliteProjectPurge,
    project_scoped_tables,
    unclassified_tables,
)
from thoth.adapters.storage.schema import metadata
from thoth.apps.runtime import AppRuntime
from thoth.domain.auth import AuthenticatedActorContext, authenticated_actor_scope
from thoth.domain.deployment_mode import DeploymentMode
from thoth.domain.upload_scope import project_upload_prefix
from thoth.protocol.jsonrpc import JsonRpcResponse, RpcErrorCode

ALPHA = "project:alpha"
BETA = "project:beta"
ALPHA_NAME = "Alpha 시험 프로젝트"
BETA_NAME = "Beta"
SHARED_OBJECT = b"bytes both projects point at"
ALPHA_OBJECT = b"bytes only alpha points at"
Snapshot = dict[str, list[str]]


def _token(project: str, table: str, column: str) -> str:
    return f"{project}|{table}|{column}"


def _row(table: Table, project: str) -> dict[str, Any]:
    """One filler row; every key is unique per project, child links point at the seeded parent."""
    link = next((item for item in CHILD_LINKS if item.child == table.name), None)
    values: dict[str, Any] = {}
    for column in table.columns:
        if column.name == "project_id":
            values[column.name] = project
        elif link is not None and column.name == link.child_column:
            values[column.name] = _token(project, link.parent, link.parent_column)
        elif isinstance(column.type, Integer):
            # Never 0 or 1: real rows already use those in unique (project, version) keys.
            values[column.name] = 1000 + int(hashlib.sha256(project.encode()).hexdigest()[:6], 16)
        elif isinstance(column.type, LargeBinary):
            values[column.name] = b"x"
        else:
            values[column.name] = _token(project, table.name, column.name)
    return values


def _seed(engine: Engine, project: str) -> None:
    """Give the project at least one row in every table that can hold project data."""
    names = {table.name for table in project_scoped_tables()}
    names |= {link.child for link in CHILD_LINKS}
    with engine.begin() as connection:
        for table in metadata.sorted_tables:
            if table.name in names and table.name != "projects":
                connection.execute(insert(table).values(**_row(table, project)))
        connection.exec_driver_sql(
            "INSERT INTO structural_fts(node_id, project_id, artifact_id, text) VALUES (?,?,?,?)",
            (f"{project}|node", project, f"{project}|artifact", "seeded structure text"),
        )


def _seed_workspace(engine: Engine) -> None:
    with engine.begin() as connection:
        for table in metadata.sorted_tables:
            if table.name in WORKSPACE_TABLES:
                connection.execute(insert(table).values(**_row(table, "workspace")))


def _snapshot(engine: Engine) -> Snapshot:
    result: Snapshot = {}
    with engine.connect() as connection:
        for table in metadata.sorted_tables:
            rows = connection.execute(select(table)).mappings()
            result[table.name] = sorted(
                json.dumps(dict(row), sort_keys=True, default=str) for row in rows
            )
        fts = connection.exec_driver_sql("SELECT node_id, project_id, text FROM structural_fts")
        result["structural_fts"] = sorted(json.dumps(list(row)) for row in fts)
    return result


def _without(snapshot: Snapshot, markers: tuple[str, ...]) -> Snapshot:
    """Rows that mention none of the markers; the deletion record table is not compared."""
    return {
        name: [row for row in rows if not any(marker in row for marker in markers)]
        for name, rows in snapshot.items()
        if name != "project_deletions"
    }


def _bookkeeping(engine: Engine) -> tuple[str, ...]:
    """What a delete request leaves behind under the workspace: its operation rows."""
    with engine.connect() as connection:
        operations = connection.execute(
            text("SELECT operation_id FROM operations WHERE project_id = 'system:workspace'")
        ).scalars()
        return ("system:workspace", *map(str, operations))


def _operation_ids(engine: Engine, project: str) -> tuple[str, ...]:
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT operation_id FROM operations WHERE project_id = :project"),
            {"project": project},
        ).scalars()
        return tuple(map(str, rows))


def _count(engine: Engine, table: Table, project: str) -> int:
    with engine.connect() as connection:
        return len(connection.execute(select(table).where(table.c.project_id == project)).all())


def _object_path(workspace: Path, payload: bytes) -> Path:
    digest = hashlib.sha256(payload).hexdigest()
    return workspace / "objects" / "sha256" / digest[:2] / digest


def _place_files(workspace: Path) -> None:
    for project in (ALPHA, BETA):
        folder = workspace / "inbox" / project_upload_prefix(project)
        folder.mkdir(parents=True)
        (folder / "note.txt").write_text(f"upload of {project}", encoding="utf-8")
    for payload in (SHARED_OBJECT, ALPHA_OBJECT):
        path = _object_path(workspace, payload)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)


async def _create(runtime: AppRuntime, project: str, name: str) -> None:
    value(
        await runtime.bus.dispatch(
            request(
                "project/create",
                f"create-{project}",
                {"project_id": project, "name": name, "cutoff_at": "2026-09-05T00:00:00Z"},
            )
        )
    )


async def _read(runtime: AppRuntime, project: str, key: str) -> dict[str, Any]:
    return value(await runtime.bus.query(request("project/read", key, {"project_id": project})))


async def _archive(runtime: AppRuntime, project: str) -> None:
    current = await _read(runtime, project, f"read-{project}")
    value(
        await runtime.bus.dispatch(
            request(
                "project/archive",
                f"archive-{project}",
                {"project_id": project, "expected_revision": current["revision"]},
            )
        )
    )


async def _delete(
    runtime: AppRuntime, project: str, name: str, key: str = "delete-1"
) -> JsonRpcResponse:
    return await runtime.bus.dispatch(
        request("project/permanentDelete", key, {"project_id": project, "confirm_name": name})
    )


async def _make_referenced(runtime: AppRuntime) -> None:
    current = await _read(runtime, BETA, "read-beta")
    value(
        await runtime.bus.dispatch(
            request(
                "project/reference/import",
                "reference",
                {
                    "project_id": BETA,
                    "expected_revision": current["revision"],
                    "origin_project_id": ALPHA,
                    "origin_revision": "r1",
                    "rights_status": "OWNED",
                    "scope": "PROJECT",
                },
            )
        )
    )


async def _two_projects(workspace: Path, *, referenced: bool = False) -> AppRuntime:
    """Alpha is archived and ready to delete; beta is a live neighbour. Both fill every table."""
    runtime = create_runtime(workspace)
    await _create(runtime, ALPHA, ALPHA_NAME)
    await _create(runtime, BETA, BETA_NAME)
    if referenced:
        await _make_referenced(runtime)
    await _archive(runtime, ALPHA)
    _place_files(workspace)
    _seed_workspace(runtime.ledger.engine)
    _seed(runtime.ledger.engine, ALPHA)
    _seed(runtime.ledger.engine, BETA)
    return runtime


def _error(response: JsonRpcResponse) -> tuple[int, dict[str, Any]]:
    assert response.error is not None
    return response.error.code, dict(response.error.data)


async def test_delete_removes_every_row_of_the_project_and_nothing_of_its_neighbour(
    tmp_path: Path,
) -> None:
    runtime = await _two_projects(tmp_path)
    try:
        engine = runtime.ledger.engine
        for table in project_scoped_tables():
            assert _count(engine, table, ALPHA) > 0, table.name
            assert _count(engine, table, BETA) > 0, table.name
        before = _snapshot(engine)
        assert any(ALPHA in row for row in before["structural_fts"])
        # Rows that belong to alpha only through its operations (owners, checkpoints) go too.
        alpha_operations = _operation_ids(engine, ALPHA)
        assert len(alpha_operations) > 1

        result = value(await _delete(runtime, ALPHA, ALPHA_NAME))
        assert result["deleted"] is True and result["already_deleted"] is False
        assert (
            result["deleted_rows"] >= len(project_scoped_tables())
            and result["cleanup_pending"] == []
        )

        after = _snapshot(engine)
        for table in project_scoped_tables():
            assert _count(engine, table, ALPHA) == 0, table.name
        # The delete's own operation record lives under the workspace, never under the project.
        markers = (ALPHA, *alpha_operations, *_bookkeeping(engine))
        leftover = _without(after, _bookkeeping(engine))
        assert not any(ALPHA in row for rows in leftover.values() for row in rows)
        assert _without(after, markers) == _without(before, markers)
        assert [BETA in row for row in after["projects"]] == [True]
        assert after["organizations"] == before["organizations"]
        assert after["tui_sessions"] == before["tui_sessions"]
        with engine.connect() as connection:
            kept = connection.execute(text("SELECT * FROM project_deletions")).mappings().all()
        assert [dict(row) for row in kept] == [
            {
                "project_id": ALPHA,
                "name_sha256": hashlib.sha256(ALPHA_NAME.encode()).hexdigest(),
                "deleted_at": result["deleted_at"],
                "cleanup_json": "[]",
            }
        ]
    finally:
        runtime.close()


async def test_files_the_project_owned_go_and_every_object_blob_stays(tmp_path: Path) -> None:
    runtime = await _two_projects(tmp_path)
    try:
        objects = [_object_path(tmp_path, item) for item in (SHARED_OBJECT, ALPHA_OBJECT)]
        value(await _delete(runtime, ALPHA, ALPHA_NAME))
        assert not (tmp_path / "inbox" / project_upload_prefix(ALPHA)).exists()
        assert (tmp_path / "inbox" / project_upload_prefix(BETA) / "note.txt").read_text(
            encoding="utf-8"
        ) == f"upload of {BETA}"
        assert [path.read_bytes() for path in objects] == [SHARED_OBJECT, ALPHA_OBJECT]
    finally:
        runtime.close()


async def test_a_failure_in_the_middle_of_the_rows_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = await _two_projects(tmp_path)
    try:
        engine = runtime.ledger.engine
        before = _snapshot(engine)
        seen: list[str] = []

        def fail_midway(self: SqliteProjectPurge, point: str) -> None:
            seen.append(point)
            if point == "purge:operations":
                raise RuntimeError("injected failure after some tables were cleared")

        monkeypatch.setattr(SqliteProjectPurge, "_fault", fail_midway)
        response = await _delete(runtime, ALPHA, ALPHA_NAME)
        assert response.error is not None and response.error.code == RpcErrorCode.INTERNAL_ERROR
        assert "purge:operations" in seen and "purge:record" not in seen
        markers = _bookkeeping(engine)
        assert _without(_snapshot(engine), markers) == _without(before, markers)
        assert (tmp_path / "inbox" / project_upload_prefix(ALPHA) / "note.txt").exists()

        monkeypatch.undo()
        assert value(await _delete(runtime, ALPHA, ALPHA_NAME, "delete-2"))["deleted"] is True
    finally:
        runtime.close()


async def test_files_that_cannot_be_removed_are_reported_and_finished_by_a_repeat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = await _two_projects(tmp_path)
    try:
        owed = "inbox/" + project_upload_prefix(ALPHA)

        def locked(path: object, *args: object, **kwargs: object) -> None:
            raise PermissionError("folder is in use")

        monkeypatch.setattr(project_purge.shutil, "rmtree", locked)
        first = value(await _delete(runtime, ALPHA, ALPHA_NAME))
        assert first["deleted"] is True and first["cleanup_pending"] == [owed]
        assert (tmp_path / owed / "note.txt").exists()
        with runtime.ledger.engine.connect() as connection:
            stored = connection.execute(
                text("SELECT cleanup_json FROM project_deletions")
            ).scalar_one()
        assert json.loads(stored) == [owed]

        monkeypatch.undo()
        again = value(await _delete(runtime, ALPHA, ALPHA_NAME, "delete-2"))
        assert again["already_deleted"] is True and again["cleanup_pending"] == []
        assert not (tmp_path / owed).exists()
    finally:
        runtime.close()


async def test_repeats_answer_already_deleted_and_the_id_is_never_reused(tmp_path: Path) -> None:
    runtime = await _two_projects(tmp_path)
    try:
        first = value(await _delete(runtime, ALPHA, ALPHA_NAME))
        assert (
            value(await _delete(runtime, ALPHA, ALPHA_NAME)) == first
        )  # same key: original result
        again = value(await _delete(runtime, ALPHA, ALPHA_NAME, "delete-2"))
        assert again["already_deleted"] is True and again["deleted_rows"] == 0
        assert again["deleted_at"] == first["deleted_at"]
        code, _ = _error(await _delete(runtime, ALPHA, "some other name", "delete-3"))
        assert code == RpcErrorCode.PROJECT_NOT_FOUND
        created = await runtime.bus.dispatch(
            request(
                "project/create",
                "recreate",
                {"project_id": ALPHA, "name": "New", "cutoff_at": "2026-09-05T00:00:00Z"},
            )
        )
        assert (
            created.error is not None and created.error.code == RpcErrorCode.PROJECT_ALREADY_EXISTS
        )
    finally:
        runtime.close()


async def test_unknown_project_is_not_found(tmp_path: Path) -> None:
    runtime = create_runtime(tmp_path)
    try:
        code, data = _error(await _delete(runtime, "project:never", "anything"))
        assert code == RpcErrorCode.PROJECT_NOT_FOUND and data["project_id"] == "project:never"
    finally:
        runtime.close()


def _make_running(runtime: AppRuntime) -> None:
    with runtime.ledger.engine.begin() as connection:
        connection.execute(
            text("UPDATE operations SET state = 'RUNNING' WHERE project_id = :project"),
            {"project": ALPHA},
        )


@pytest.mark.parametrize(
    ("name", "reason", "extra"),
    [
        ("Alpha 시험 프로젝트 ", "PROJECT_NAME_MISMATCH", {}),
        ("alpha 시험 프로젝트", "PROJECT_NAME_MISMATCH", {}),
        (ALPHA_NAME, "PROJECT_REFERENCED_BY_OTHER_PROJECTS", {"referencing_project_ids": [BETA]}),
        (ALPHA_NAME, "PROJECT_HAS_ACTIVE_OPERATION", {}),
    ],
)
async def test_refusals_leave_every_row_as_it_was(
    tmp_path: Path, name: str, reason: str, extra: dict[str, Any]
) -> None:
    runtime = await _two_projects(
        tmp_path, referenced=reason == "PROJECT_REFERENCED_BY_OTHER_PROJECTS"
    )
    try:
        if reason == "PROJECT_HAS_ACTIVE_OPERATION":
            _make_running(runtime)
        engine = runtime.ledger.engine
        before = _snapshot(engine)
        code, data = _error(await _delete(runtime, ALPHA, name))
        assert code == RpcErrorCode.DOMAIN_REJECTED
        assert data == {"reason_code": reason, **extra}
        markers = _bookkeeping(engine)
        assert _without(_snapshot(engine), markers) == _without(before, markers)
        assert (tmp_path / "inbox" / project_upload_prefix(ALPHA) / "note.txt").exists()
    finally:
        runtime.close()


async def test_only_an_archived_project_can_be_deleted(tmp_path: Path) -> None:
    runtime = create_runtime(tmp_path)
    try:
        await _create(runtime, ALPHA, ALPHA_NAME)
        engine = runtime.ledger.engine
        before = _snapshot(engine)
        code, data = _error(await _delete(runtime, ALPHA, ALPHA_NAME))
        assert (
            code == RpcErrorCode.DOMAIN_REJECTED and data["reason_code"] == "PROJECT_NOT_ARCHIVED"
        )
        markers = _bookkeeping(engine)
        assert _without(_snapshot(engine), markers) == _without(before, markers)
    finally:
        runtime.close()


async def test_hosted_review_refuses_before_any_record_is_written(tmp_path: Path) -> None:
    runtime = create_runtime(
        tmp_path, deployment_mode=DeploymentMode.HOSTED_REVIEW, model_catalog=StaticModelCatalog()
    )
    try:
        await _create(runtime, ALPHA, ALPHA_NAME)
        await _archive(runtime, ALPHA)
        before = _snapshot(runtime.ledger.engine)
        code, data = _error(await _delete(runtime, ALPHA, ALPHA_NAME))
        assert code == RpcErrorCode.AUTHORIZATION_DENIED
        assert data["reason_code"] == "PROJECT_DELETE_LOCAL_ONLY"
        assert _snapshot(runtime.ledger.engine) == before  # not even an operation record
    finally:
        runtime.close()


async def test_a_caller_without_authority_over_the_project_is_refused_first(
    tmp_path: Path,
) -> None:
    runtime = create_runtime(tmp_path)
    try:
        await _create(runtime, ALPHA, ALPHA_NAME)
        await _archive(runtime, ALPHA)
        before = _snapshot(runtime.ledger.engine)
        stranger = AuthenticatedActorContext(
            actor_id="actor",
            session_id="session",
            project_id=BETA,
            role_assignment_id="role",
            role="OWNER",
            capabilities=("READ", "WRITE", "ADMIN"),
            data_scopes=("PROJECT",),
        )
        with authenticated_actor_scope(stranger):
            code, data = _error(await _delete(runtime, ALPHA, ALPHA_NAME))
        assert code == RpcErrorCode.AUTHORIZATION_DENIED
        assert data["reason_code"] == "PROJECT_DELETE_SCOPE_DENIED"
        assert _snapshot(runtime.ledger.engine) == before
    finally:
        runtime.close()


async def test_a_table_the_purge_does_not_know_stops_it_before_anything_is_deleted(
    tmp_path: Path,
) -> None:
    runtime = await _two_projects(tmp_path)
    try:
        engine = runtime.ledger.engine
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE later_added (project_id TEXT)")
        before = _snapshot(engine)
        code, data = _error(await _delete(runtime, ALPHA, ALPHA_NAME))
        assert code == RpcErrorCode.DOMAIN_REJECTED
        assert data["reason_code"] == "PROJECT_PURGE_TABLE_UNCLASSIFIED:later_added"
        markers = _bookkeeping(engine)
        assert _without(_snapshot(engine), markers) == _without(before, markers)
    finally:
        runtime.close()


def test_every_table_of_the_schema_is_deleted_by_project_kept_or_linked_to_a_parent(
    tmp_path: Path,
) -> None:
    runtime = create_runtime(tmp_path)
    try:
        with runtime.ledger.engine.connect() as connection:
            stored = {
                str(name)
                for name in connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).scalars()
            }
        assert unclassified_tables(stored | {table.name for table in metadata.sorted_tables}) == ()
        assert unclassified_tables({"brand_new_table"}) == ("brand_new_table",)
        scoped = {table.name for table in project_scoped_tables()}
        assert scoped.isdisjoint(WORKSPACE_TABLES | {link.child for link in CHILD_LINKS})
        assert "project_deletions" not in scoped and "projects" in scoped
        for link in CHILD_LINKS:
            child, parent = metadata.tables[link.child], metadata.tables[link.parent]
            assert "project_id" not in child.c and link.child_column in child.c
            assert "project_id" in parent.c and link.parent_column in parent.c
        for name in WORKSPACE_TABLES:
            assert "project_id" not in metadata.tables[name].c
    finally:
        runtime.close()
