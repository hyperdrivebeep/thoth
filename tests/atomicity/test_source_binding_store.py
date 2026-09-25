"""The legacy/direct binding port shares history publication, without granting source access."""

from pathlib import Path

import pytest
from sqlalchemy import Connection
from tests.atomicity.harness import assert_phase_delta, snapshot
from tests.integration.storage_coverage_helpers import prepare_project

from thoth.adapters.parsers.registry import default_parser_registry
from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.storage.bundle import SqliteStoreBundle, SqliteStoreFactory
from thoth.adapters.storage.governance_history import SqliteGovernanceHistory
from thoth.application.services.ingestion_service import IngestArtifactCommand, IngestionService
from thoth.domain.enums import AuthorityState, CutoffState, SecurityClass
from thoth.domain.governance import SourceBinding
from thoth.domain.governance_revision import GovernanceReceipt, GovernanceRevision


@pytest.mark.parametrize("existing", [False, True])
async def test_direct_binding_insert_and_update_share_history_rollback_and_reopen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing: bool
) -> None:
    runtime, project = await prepare_project(tmp_path)
    stores = SqliteStoreBundle(runtime.ledger, tmp_path)
    clock, ids = SystemClock(), UuidIdGenerator()
    ingestion = IngestionService(
        projects=stores.projects,
        objects=stores.objects,
        parsers=default_parser_registry(),
        artifacts=stores.artifacts,
        clock=clock,
        ids=ids,
    )
    artifact = ingestion.ingest(
        IngestArtifactCommand(
            project_id=project,
            source_uri="fixture/legacy.md",
            media_type="text/markdown",
            raw=b"# Legacy\nObserved source.",
            authority=AuthorityState.OFFICIAL,
            cutoff_state=CutoffState.ELIGIBLE,
            security_class=SecurityClass.INTERNAL,
            operation_id="operation:legacy-ingest",
            source_path=Path("legacy.md"),
        )
    ).document.artifact
    binding = SourceBinding(
        binding_id="binding:direct",
        project_id=project,
        artifact_id=artifact.artifact_id,
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    try:
        if existing:
            stores.governance.put_source_binding(binding)
            binding = binding.model_copy(update={"state": "DETACHED", "updated_at": clock.now()})
        before = snapshot(runtime.ledger.engine)
        original = SqliteGovernanceHistory._insert_record  # pyright: ignore[reportPrivateUsage]

        def fail(
            connection: Connection, revision: GovernanceRevision, receipt: GovernanceReceipt
        ) -> None:
            original(connection, revision, receipt)
            raise RuntimeError("binding history fault")

        with monkeypatch.context() as patch:
            patch.setattr(SqliteGovernanceHistory, "_insert_record", staticmethod(fail))
            with pytest.raises(RuntimeError, match="binding history fault"):
                stores.governance.put_source_binding(binding)
        assert_phase_delta(before, snapshot(runtime.ledger.engine))
        stores.governance.put_source_binding(binding)
        assert stores.governance.list_source_bindings(project) == (binding,)
        # A binding is not an ACL: legacy ingestion did not manufacture scope ownership.
        assert stores.resource_scopes.read(project, artifact.artifact_id) is None
    finally:
        runtime.close()
    reopened = SqliteStoreFactory().open(tmp_path)
    try:
        assert reopened.governance.list_source_bindings(project) == (binding,)
        history = SqliteGovernanceHistory(reopened.ledger.engine).history(
            project, "SOURCE_BINDING", binding.binding_id
        )
        assert len(history) == (2 if existing else 1)
        assert history[-1].projection["state"] == binding.state
        if existing:
            assert history[-1].previous_digest == history[0].record_digest
        assert reopened.resource_scopes.read(project, artifact.artifact_id) is None
    finally:
        reopened.close()
