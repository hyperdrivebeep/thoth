"""Exercise shared publication hooks through real ProjectPack and sandbox use cases."""

from pathlib import Path

import pytest
from tests.integration.test_connector_sandbox_cycle import (
    test_connector_receipt_and_r2_sandbox_auto_completion as sandbox_scenario,
)
from tests.integration.test_projectpack_run import (
    test_example_projectpack_runs_without_core_scenario_hardcoding as pack_scenario,
)

from thoth.adapters.storage.artifacts import SqliteArtifactLedger
from thoth.adapters.storage.embedding_index import SqliteEmbeddingIndex
from thoth.adapters.storage.transaction import has_ambient_transaction
from thoth.domain.artifact import StructuralDocument
from thoth.domain.embedding_index import EmbeddingIndexConfig
from thoth.domain.evidence import EvidenceSpan


@pytest.mark.parametrize("scenario", ["pack", "sandbox"])
@pytest.mark.parametrize("enabled", [False, True])
async def test_publication_stages_only_intent_in_original_uow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    enabled: bool,
) -> None:
    observed: list[tuple[str, int]] = []
    original = SqliteArtifactLedger.persist_ingestion

    # Enable only the synthetic fixture's project at its first published source. The actual
    # application caller, scope admission, UoW, storage hook and binding logic remain real.
    def publish(
        self: SqliteArtifactLedger,
        document: StructuralDocument,
        source_version_id: str,
        evidence_candidates: tuple[EvidenceSpan, ...],
    ) -> None:
        from typing import cast

        from sqlalchemy import Engine

        store = SqliteEmbeddingIndex(cast(Engine, vars(self)["_engine"]))
        project = document.artifact.project_id
        assert has_ambient_transaction()
        if enabled and not store.configuration(project).enabled:
            store.configure(
                EmbeddingIndexConfig(project_id=project, enabled=True, actor_ref="local:operator"),
                0,
            )
        original(self, document, source_version_id, evidence_candidates)
        jobs = tuple(
            j for j in store.jobs(project) if j.intent.source_version_id == source_version_id
        )
        assert len(jobs) == int(enabled)
        if jobs:
            assert jobs[0].state == "PENDING" and jobs[0].manifest_digest is None
        observed.append((document.artifact.source_uri, len(jobs)))

    monkeypatch.setattr(SqliteArtifactLedger, "persist_ingestion", publish)
    if scenario == "pack":
        await pack_scenario(tmp_path)
        assert observed
    else:
        await sandbox_scenario(tmp_path)
        assert any(uri.startswith("sandbox://") for uri, _ in observed)
