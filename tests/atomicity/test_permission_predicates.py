"""Read predicates preserve filtering and transaction/resource-use failures."""

from pathlib import Path

import pytest
from tests.atomicity.harness import assert_phase_delta, snapshot
from tests.integration.storage_coverage_helpers import prepare_project, request, value

from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.storage.bundle import SqliteStoreBundle
from thoth.application.services.control_record_service import ControlRecordService
from thoth.application.services.resource_scope_read_context import ScopeReadContext
from thoth.application.services.resource_scope_service import ResourceScopeService
from thoth.apps.runtime import AppRuntime
from thoth.domain.resource_scope import (
    ResourceScopeError,
    ResourceScopeRecord,
    ResourceUse,
    current_resource_uses,
    record_resource_use,
    resource_use_scope,
)


async def prepare(
    root: Path,
) -> tuple[AppRuntime, ResourceScopeService, ControlRecordService, str, str]:
    runtime, project = await prepare_project(root)
    inbox = root / "inbox"
    inbox.mkdir(exist_ok=True)
    (inbox / "readable.md").write_text("# Source\nA bounded local observation.", encoding="utf-8")
    artifact = value(
        await runtime.bus.dispatch(
            request(
                "project/source/connect",
                "source",
                {
                    "project_id": project,
                    "relative_path": "readable.md",
                    "media_type": "text/markdown",
                    "authority": "OFFICIAL",
                    "cutoff_state": "ELIGIBLE",
                },
            )
        )
    )["artifact"]["artifact_id"]
    stores = SqliteStoreBundle(runtime.ledger, root)
    clock, ids = SystemClock(), UuidIdGenerator()
    scopes = ResourceScopeService(
        store=stores.resource_scopes,
        artifacts=stores.artifacts,
        projects=stores.projects,
        governance=stores.governance,
        sessions=stores.auth,
        ledger=stores.ledger,
        clock=clock,
        ids=ids,
        evidence=stores.evidence,
    )
    controls = ControlRecordService(store=stores.controls, clock=clock, ids=ids)
    return runtime, scopes, controls, project, str(artifact)


def marker(controls: ControlRecordService, project: str) -> None:
    controls.create(
        project_id=project,
        namespace="ATOMICITY_TEST",
        record_type="MARKER",
        record_id="marker:predicate",
        state="SAVED",
        payload={"value": "retained"},
    )


async def test_denied_predicate_has_no_read_use_and_does_not_poison_parent_commit(
    tmp_path: Path,
) -> None:
    runtime, scopes, controls, project, _ = await prepare(tmp_path)
    try:
        with resource_use_scope(project):
            with runtime.ledger.transaction():
                marker(controls, project)
                assert scopes.may_read(project, "artifact:missing") is False
                assert current_resource_uses() == ()
            assert current_resource_uses() == ()
        assert controls.read(project, "ATOMICITY_TEST", "marker:predicate") is not None
    finally:
        runtime.close()


async def test_allowed_predicate_records_exact_read_use(tmp_path: Path) -> None:
    runtime, scopes, controls, project, artifact = await prepare(tmp_path)
    try:
        with resource_use_scope(project), runtime.ledger.transaction():
            marker(controls, project)
            assert scopes.may_read(project, artifact) is True
            assert current_resource_uses() == (
                ResourceUse(project_id=project, resource_ref=artifact, capability="READ"),
            )
        assert controls.read(project, "ATOMICITY_TEST", "marker:predicate") is not None
    finally:
        runtime.close()


@pytest.mark.parametrize("code", ["RESOURCE_AUTHORITY_CHANGED", "RESOURCE_SCOPE_LINEAGE_INVALID"])
async def test_unexpected_predicate_denial_still_invalidates_swallowed_parent_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    runtime, scopes, controls, project, artifact = await prepare(tmp_path)
    before = snapshot(runtime.ledger.engine)

    def reject(
        self: ResourceScopeService,
        project_id: str,
        resource_ref: str,
        context: ScopeReadContext | None = None,
    ) -> ResourceScopeRecord:
        raise ResourceScopeError(code)

    try:
        monkeypatch.setattr(ResourceScopeService, "_required_record", reject)
        with resource_use_scope(project):
            with pytest.raises(RuntimeError, match="rollback-only"), runtime.ledger.transaction():
                marker(controls, project)
                with pytest.raises(ResourceScopeError, match=code):
                    scopes.may_read(project, artifact)
            assert current_resource_uses() == ()
        assert_phase_delta(before, snapshot(runtime.ledger.engine))
    finally:
        runtime.close()


@pytest.mark.parametrize("boundary", ["project", "usage"])
async def test_resource_use_recording_error_is_not_a_false_predicate(
    tmp_path: Path, boundary: str
) -> None:
    runtime, scopes, controls, project, artifact = await prepare(tmp_path)
    before = snapshot(runtime.ledger.engine)
    code = (
        "RESOURCE_SCOPE_PROJECT_MISMATCH" if boundary == "project" else "RESOURCE_SCOPE_USAGE_LIMIT"
    )
    try:
        with resource_use_scope("project:other" if boundary == "project" else project):
            if boundary == "usage":
                for index in range(4096):
                    record_resource_use(project, f"resource:{index}", "READ")
            prior_uses = current_resource_uses()
            with pytest.raises(RuntimeError, match="rollback-only"), runtime.ledger.transaction():
                marker(controls, project)
                with pytest.raises(ResourceScopeError, match=code):
                    scopes.may_read(project, artifact)
            assert current_resource_uses() == prior_uses
        assert_phase_delta(before, snapshot(runtime.ledger.engine))
    finally:
        runtime.close()
