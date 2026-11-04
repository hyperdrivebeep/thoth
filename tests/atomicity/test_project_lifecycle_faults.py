"""Project governance changes and the permanent delete commit their database work as one unit.

The permanent delete is two phases by design: one database transaction, then file cleanup. The
cleanup step is recorded in the deletion record, so a failure there is finished by repeating the
request.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from tests.atomicity.fault_helpers import assert_only_failed_command_bookkeeping
from tests.atomicity.harness import Snapshot, snapshot
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import fail_after, request, value

from thoth.adapters.storage.governance import SqliteGovernanceStore
from thoth.adapters.storage.project_purge import SqliteProjectPurge
from thoth.adapters.storage.projects import SqliteProjectStore
from thoth.apps.runtime import AppRuntime
from thoth.domain.upload_scope import project_upload_prefix

PROJECT = "project:lifecycle-faults"
NAME = "Lifecycle faults"


async def call(
    runtime: AppRuntime, method: str, key: str, body: dict[str, object]
) -> dict[str, Any]:
    return value(await runtime.bus.dispatch(request(method, key, body)))


async def open_project(workspace: Path) -> AppRuntime:
    runtime = create_runtime(workspace)
    created: dict[str, object] = {
        "project_id": PROJECT,
        "name": NAME,
        "cutoff_at": "2026-09-05T00:00:00Z",
    }
    await call(runtime, "project/create", "create", created)
    return runtime


async def revision_of(runtime: AppRuntime, key: str) -> int:
    body: dict[str, object] = {"project_id": PROJECT}
    return int((await call(runtime, "project/read", key, body))["revision"])


async def read_project(runtime: AppRuntime, key: str) -> dict[str, Any]:
    return await call(runtime, "project/read", key, {"project_id": PROJECT})


@pytest.mark.parametrize("method", ["project/archive", "project/metadata/update"])
@pytest.mark.parametrize("point", ["update"])
async def test_project_state_change_fault_after_the_write_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method: str, point: str
) -> None:
    runtime = await open_project(tmp_path / "workspace")
    try:
        revision = await revision_of(runtime, "read-0")
        body: dict[str, object] = {"project_id": PROJECT, "expected_revision": revision}
        if method == "project/metadata/update":
            body["name"] = "Renamed by the test"
        stored_before = await read_project(runtime, "read-before")
        command = request(method, "fault", body)
        before = snapshot(runtime.ledger.engine)
        with monkeypatch.context() as patch:
            hits = fail_after(patch, SqliteProjectStore, point)
            result = await runtime.bus.dispatch(command)
        assert result.error is not None and hits == [point]
        assert_only_failed_command_bookkeeping(runtime.ledger.engine, before, command)
        assert await read_project(runtime, "read-after") == stored_before
        retried = await call(runtime, method, "retry", body)
        assert retried["revision"] == revision + 1
    finally:
        runtime.close()


async def test_role_assignment_fault_after_the_write_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = await open_project(tmp_path / "workspace")
    try:
        revision = await revision_of(runtime, "read-0")
        body: dict[str, object] = {
            "project_id": PROJECT,
            "expected_revision": revision,
            "actor_id": "human:project-owner",
            "role": "project-owner",
            "scope": "PROJECT",
            "authority_tags": ["R3_ACTION_OWNER"],
        }
        command = request("project/role/assign", "fault", body)
        before = snapshot(runtime.ledger.engine)
        with monkeypatch.context() as patch:
            hits = fail_after(patch, SqliteGovernanceStore, "create_role")
            result = await runtime.bus.dispatch(command)
        assert result.error is not None and hits == ["create_role"]
        assert_only_failed_command_bookkeeping(runtime.ledger.engine, before, command)
        assert await revision_of(runtime, "read-after") == revision
        assigned = await call(runtime, "project/role/assign", "retry", body)
        assert assigned["role"]["role"] == "project-owner"
    finally:
        runtime.close()


async def archived_project_with_files(workspace: Path) -> tuple[AppRuntime, Path]:
    runtime = await open_project(workspace)
    revision = await revision_of(runtime, "read-0")
    archive: dict[str, object] = {"project_id": PROJECT, "expected_revision": revision}
    await call(runtime, "project/archive", "archive", archive)
    folder = workspace / "inbox" / project_upload_prefix(PROJECT)
    folder.mkdir(parents=True)
    (folder / "note.txt").write_text("an upload of this project", encoding="utf-8")
    return runtime, folder / "note.txt"


JOURNAL = {
    "events",
    "idempotency_keys",
    "operation_owners",
    "operation_resource_bindings",
    "operations",
}


def without_journal(state: Snapshot) -> Snapshot:
    """A permanent delete journals under the workspace, not under the project it deletes."""
    return {table: rows for table, rows in state.items() if table not in JOURNAL}


def delete_body() -> dict[str, object]:
    return {"project_id": PROJECT, "confirm_name": NAME}


def delete_command(key: str):
    return request("project/permanentDelete", key, delete_body())


async def test_delete_fault_before_the_rows_commit_changes_nothing_and_keeps_the_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, upload = await archived_project_with_files(tmp_path / "workspace")
    try:
        command = delete_command("fault")
        before = snapshot(runtime.ledger.engine)
        calls = {"n": 0}

        def fail_at_record(self: SqliteProjectPurge, point: str) -> None:
            if point == "purge:record":
                calls["n"] += 1
                raise RuntimeError("injected fault after the rows were deleted")

        with monkeypatch.context() as patch:
            patch.setattr(SqliteProjectPurge, "_fault", fail_at_record)
            result = await runtime.bus.dispatch(command)
        assert result.error is not None and calls["n"] == 1
        assert without_journal(snapshot(runtime.ledger.engine)) == without_journal(before)
        assert upload.exists(), "no file is touched before the rows are committed"
        deleted = await call(runtime, "project/permanentDelete", "retry", delete_body())
        assert deleted["deleted"] is True and deleted["already_deleted"] is False
        assert not upload.exists() and deleted["cleanup_pending"] == []
    finally:
        runtime.close()


async def test_delete_fault_after_the_commit_leaves_a_record_that_a_repeat_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, upload = await archived_project_with_files(tmp_path / "workspace")
    try:
        with monkeypatch.context() as patch:

            def fail_cleanup(self: SqliteProjectPurge, project_id: str) -> tuple[str, ...]:
                raise RuntimeError("injected fault between the commit and the file cleanup")

            patch.setattr(SqliteProjectPurge, "clean_files", fail_cleanup)
            result = await runtime_dispatch(runtime, delete_command("fault"))
        assert result.error is not None
        # By design the rows are gone and the record names the upload folder still to remove.
        engine = runtime.ledger.engine
        state = snapshot(engine)
        assert state["projects"] == (), "the project rows were committed away"
        assert (
            len(state["project_deletions"]) == 1
            and project_upload_prefix(PROJECT) in state["project_deletions"][0]
        )
        assert upload.exists(), "the file waits for the repeat"
        repeated = await call(runtime, "project/permanentDelete", "repeat", delete_body())
        assert repeated["already_deleted"] is True and repeated["cleanup_pending"] == []
        assert not upload.exists()
        assert len(snapshot(engine)["project_deletions"]) == 1
    finally:
        runtime.close()


async def runtime_dispatch(runtime: AppRuntime, command: Any):
    return await runtime.bus.dispatch(command)
