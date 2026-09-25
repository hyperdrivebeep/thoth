from __future__ import annotations

from pathlib import Path

from thoth.adapters.behavior_catalog import FilesystemBehaviorArtifactCatalog
from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.storage import SqliteBehaviorArtifactStore
from thoth.application.services.behavior_candidate_service import BehaviorCandidateService
from thoth.apps.runtime import create_runtime
from thoth.domain.behavior_artifact import BehaviorArtifactKind


def test_retrieval_policy_candidate_is_versioned_and_project_scoped(tmp_path: Path) -> None:
    runtime = create_runtime(tmp_path / "retrieval-candidate")
    try:
        store = SqliteBehaviorArtifactStore(runtime.ledger.engine)
        service = BehaviorCandidateService(
            store=store,
            clock=SystemClock(),
            ids=UuidIdGenerator(),
            catalog=FilesystemBehaviorArtifactCatalog(Path("config")),
        )
        first = service.ensure_baseline(
            "project:a08:retrieval:first",
            BehaviorArtifactKind.RETRIEVAL_POLICY,
        )
        second = service.ensure_baseline(
            "project:a08:retrieval:second",
            BehaviorArtifactKind.RETRIEVAL_POLICY,
        )
        assert first.content_digest == second.content_digest
        assert first.artifact_id != second.artifact_id
        candidate = service.create_candidate(
            project_id=first.project_id,
            kind=first.kind,
            content={**first.content, "version": "1.0.1", "max_selected": 6},
            parent_digest=first.content_digest,
        )
        assert candidate.parent_digest == first.content_digest
        assert candidate.content_digest != first.content_digest
        assert candidate.content["canonical_projection"] is False
    finally:
        runtime.close()
