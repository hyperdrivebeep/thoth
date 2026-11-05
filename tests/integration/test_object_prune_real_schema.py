"""Object pruning against the real schema, and refusal while another process owns the workspace."""

from __future__ import annotations

import hashlib
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
import typer
from tests.integration.test_workspace_runtime_lock import (
    _LOCK_HOLDER,  # pyright: ignore[reportPrivateUsage]
    _holder,  # pyright: ignore[reportPrivateUsage]
)
from typer.testing import CliRunner

from thoth.adapters.parsers.registry import default_parser_registry
from thoth.adapters.storage import (
    ContentAddressedObjectStore,
    SqliteArtifactLedger,
    SqliteLedger,
    SqliteProjectStore,
    migrate_sqlite_database,
)
from thoth.adapters.storage.object_prune import (
    MIN_AGE_SECONDS,
    delete_confirmed_objects,
    preview_unreferenced_objects,
)
from thoth.adapters.storage.workspace_owner import WorkspaceInUse
from thoth.application.services import IngestArtifactCommand, IngestionService
from thoth.apps.object_prune_commands import register_object_prune_commands
from thoth.domain.enums import AuthorityState, CutoffState, SecurityClass
from thoth.domain.project import Project


class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 8, 30, 6, 45, tzinfo=UTC)


class _Ids:
    def __init__(self) -> None:
        self.count = 0

    def new(self, prefix: str) -> str:
        self.count += 1
        return f"{prefix}:{self.count}"


def _age(path: Path) -> None:
    old = time.time() - 3 * MIN_AGE_SECONDS
    os.utime(path, (old, old))


def _workspace_with_one_artifact(tmp_path: Path) -> tuple[Path, str]:
    workspace = tmp_path / "workspace"
    database = workspace / "db" / "thoth.sqlite3"
    migrate_sqlite_database(database)
    ledger = SqliteLedger(database)
    ledger.initialize()
    projects = SqliteProjectStore(ledger.engine)
    projects.create(
        Project(
            project_id="project:p",
            name="p",
            cutoff_at=datetime(2026, 8, 30, tzinfo=UTC),
            overlay="general-rnd",
            policy_binding_ref="policy:default",
        ),
        created_at="2026-08-30T06:45:00+00:00",
    )
    service = IngestionService(
        projects=projects,
        objects=ContentAddressedObjectStore(workspace),
        parsers=default_parser_registry(),
        artifacts=SqliteArtifactLedger(ledger.engine),
        clock=_Clock(),
        ids=_Ids(),
    )
    result = service.ingest(
        IngestArtifactCommand(
            project_id="project:p",
            source_uri="fixture/x.md",
            media_type="text/markdown",
            raw=b"# plan\n\ntarget accuracy is at least 94 percent.\n",
            authority=AuthorityState.OFFICIAL,
            cutoff_state=CutoffState.ELIGIBLE,
            security_class=SecurityClass.INTERNAL,
            operation_id="operation:a",
            source_path=Path("x.md"),
        )
    )
    ledger.engine.dispose()
    return workspace, result.object_digest


def _orphan(workspace: Path) -> str:
    raw = b"an evaluation output whose receipt was never saved\n"
    digest = hashlib.sha256(raw).hexdigest()
    _age(ContentAddressedObjectStore(workspace).put(raw, digest, operation_id="operation:orphan"))
    return digest


def _object_path(workspace: Path, digest: str) -> Path:
    return workspace / "objects" / "sha256" / digest[:2] / digest


def test_real_workspace_keeps_the_ingested_object_and_removes_only_the_orphan(tmp_path: Path):
    workspace, referenced = _workspace_with_one_artifact(tmp_path)
    _age(_object_path(workspace, referenced))
    orphan = _orphan(workspace)
    preview = preview_unreferenced_objects(workspace)
    assert preview.complete and [entry.digest for entry in preview.candidates] == [orphan]
    delete_confirmed_objects(workspace, preview.list_sha256 or "")
    assert _object_path(workspace, referenced).is_file()
    assert not _object_path(workspace, orphan).exists()


def test_a_process_that_owns_the_workspace_blocks_preview_and_delete(tmp_path: Path):
    workspace, referenced = _workspace_with_one_artifact(tmp_path)
    _age(_object_path(workspace, referenced))
    orphan = _orphan(workspace)
    preview = preview_unreferenced_objects(workspace)
    runner = CliRunner()
    app = typer.Typer()
    register_object_prune_commands(app, workspace)
    with _holder(tmp_path, workspace, _LOCK_HOLDER):
        with pytest.raises(WorkspaceInUse):
            preview_unreferenced_objects(workspace)
        with pytest.raises(WorkspaceInUse):
            delete_confirmed_objects(workspace, preview.list_sha256 or "")
        shown = runner.invoke(app, ["objects", "prune"])
        confirmed = runner.invoke(app, ["objects", "prune", "--confirm", preview.list_sha256 or ""])
        assert shown.exit_code == 3 and "already in use" in shown.output
        assert confirmed.exit_code == 3
        assert _object_path(workspace, orphan).is_file()
    after = delete_confirmed_objects(workspace, preview.list_sha256 or "")
    assert after.deleted_count == 1
