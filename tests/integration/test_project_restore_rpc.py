from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text
from tests.integration.storage_coverage_helpers import (
    domain_snapshot,
    prepare_project,
    request,
    value,
)

from thoth.adapters.storage.governance import SqliteGovernanceStore
from thoth.adapters.storage.projects import SqliteProjectStore
from thoth.apps.runtime import AppRuntime
from thoth.domain.enums import ProjectLifecycle
from thoth.domain.governance import SourceBinding
from thoth.protocol.jsonrpc import RpcErrorCode


async def _archive(runtime: AppRuntime, project: str, key: str) -> dict[str, object]:
    current = value(
        await runtime.bus.query(request("project/read", key + "-read", {"project_id": project}))
    )
    return value(
        await runtime.bus.dispatch(
            request(
                "project/archive",
                key,
                {"project_id": project, "expected_revision": current["revision"]},
            )
        )
    )


async def _restore(runtime: AppRuntime, project: str, key: str, revision: object):
    return await runtime.bus.dispatch(
        request("project/restore", key, {"project_id": project, "expected_revision": revision})
    )


def _no_record(self: SqliteProjectStore, project_id: str) -> None:
    """A history that cannot say what the project was before the archive."""
    return None


def _move_to(runtime: AppRuntime, project: str, lifecycle: ProjectLifecycle) -> None:
    """Move the project through the store, so the move lands in the governance history."""
    store = SqliteProjectStore(runtime.ledger.engine)
    current = store.read(project)
    assert current is not None
    moved = current.model_copy(update={"lifecycle": lifecycle, "revision": current.revision + 1})
    assert store.update(moved, expected_revision=current.revision)


def _lifecycles(runtime: AppRuntime, project: str) -> list[str]:
    with runtime.ledger.engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT json_extract(content_json, '$.projection.lifecycle') "
                "FROM governance_revisions "
                "WHERE project_id=:project AND record_kind='PROJECT' ORDER BY revision"
            ),
            {"project": project},
        ).scalars()
        return [str(item) for item in rows]


async def test_restore_brings_back_the_lifecycle_before_the_archive_and_keeps_the_project_usable(
    tmp_path: Path,
) -> None:
    runtime, project = await prepare_project(tmp_path)
    try:
        archived = await _archive(runtime, project, "archive-1")
        restored = value(await _restore(runtime, project, "restore-1", archived["revision"]))
        assert restored["lifecycle"] == "DRAFT"
        assert restored["revision"] == archived["revision"] + 1  # type: ignore[operator]
        # the history shows the whole path, so the restore is visible as its own revision
        assert _lifecycles(runtime, project) == ["DRAFT", "ARCHIVED_READ_ONLY", "DRAFT"]
        listed = value(
            await runtime.bus.query(
                request("project/list", "list", {"project_id": "system:projects"})
            )
        )
        assert [item["project_id"] for item in listed["projects"]] == [project]
        renamed = value(
            await runtime.bus.dispatch(
                request(
                    "project/metadata/update",
                    "rename-after-restore",
                    {
                        "project_id": project,
                        "expected_revision": restored["revision"],
                        "name": "Back again",
                    },
                )
            )
        )
        assert renamed["name"] == "Back again"
    finally:
        runtime.close()


@pytest.mark.parametrize("before", [ProjectLifecycle.ACTIVE, ProjectLifecycle.CLOSING])
async def test_restore_returns_to_the_recorded_active_or_closing_state(
    tmp_path: Path, before: ProjectLifecycle
) -> None:
    runtime, project = await prepare_project(tmp_path)
    try:
        _move_to(runtime, project, before)
        archived = await _archive(runtime, project, "archive-1")
        restored = value(await _restore(runtime, project, "restore-1", archived["revision"]))
        assert restored["lifecycle"] == before.value
        assert _lifecycles(runtime, project)[-3:] == [
            before.value,
            "ARCHIVED_READ_ONLY",
            before.value,
        ]
        # archive again and restore again: the newest earlier lifecycle is used, not the first one
        again = await _archive(runtime, project, "archive-2")
        assert (
            value(await _restore(runtime, project, "restore-2", again["revision"]))["lifecycle"]
            == before.value
        )
    finally:
        runtime.close()


async def test_restore_without_a_readable_record_uses_the_activation_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(SqliteProjectStore, "prior_lifecycle", _no_record)
    runtime, project = await prepare_project(tmp_path)
    try:
        archived = await _archive(runtime, project, "archive-1")
        # a policy exists but no active source is connected: draft
        assert (
            value(await _restore(runtime, project, "restore-1", archived["revision"]))["lifecycle"]
            == "DRAFT"
        )
        moment = datetime(2026, 9, 5, tzinfo=UTC)
        SqliteGovernanceStore(runtime.ledger.engine).put_source_binding(
            SourceBinding(
                binding_id="binding:1",
                project_id=project,
                artifact_id="artifact:1",
                created_at=moment,
                updated_at=moment,
            )
        )
        again = await _archive(runtime, project, "archive-2")
        assert (
            value(await _restore(runtime, project, "restore-2", again["revision"]))["lifecycle"]
            == "ACTIVE"
        )
    finally:
        runtime.close()


async def test_restore_of_a_project_that_is_not_archived_returns_it_unchanged(
    tmp_path: Path,
) -> None:
    runtime, project = await prepare_project(tmp_path)
    try:
        before = domain_snapshot(runtime.ledger.engine)
        current = value(
            await runtime.bus.query(request("project/read", "read", {"project_id": project}))
        )
        same = value(await _restore(runtime, project, "restore-not-archived", 999))
        assert same["lifecycle"] == "DRAFT" and same["revision"] == current["revision"]
        assert domain_snapshot(runtime.ledger.engine) == before
    finally:
        runtime.close()


async def test_restore_with_a_stale_revision_is_rejected_and_changes_nothing(
    tmp_path: Path,
) -> None:
    runtime, project = await prepare_project(tmp_path)
    try:
        archived = await _archive(runtime, project, "archive-1")
        before = domain_snapshot(runtime.ledger.engine)
        rejected = await _restore(runtime, project, "restore-stale", int(archived["revision"]) - 1)  # type: ignore[call-overload]
        assert rejected.error is not None
        assert rejected.error.message == "project revision does not match expected_revision"
        assert domain_snapshot(runtime.ledger.engine) == before
        readback = value(
            await runtime.bus.query(request("project/read", "readback", {"project_id": project}))
        )
        assert readback["lifecycle"] == "ARCHIVED_READ_ONLY"
    finally:
        runtime.close()


async def test_restore_of_an_unknown_project_is_not_found(tmp_path: Path) -> None:
    runtime, _project = await prepare_project(tmp_path)
    try:
        missing = await _restore(runtime, "project:missing", "restore-missing", 0)
        assert missing.error is not None
        assert missing.error.code == RpcErrorCode.PROJECT_NOT_FOUND
    finally:
        runtime.close()
