from __future__ import annotations

from pathlib import Path

from thoth.adapters.behavior_catalog import FilesystemBehaviorArtifactCatalog
from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.storage import SqliteBehaviorArtifactStore
from thoth.application.services.behavior_candidate_service import BehaviorCandidateService
from thoth.application.services.improvement_runner import ImprovementRunner
from thoth.apps.runtime import create_runtime
from thoth.domain.behavior_artifact import BehaviorArtifactKind


def test_failed_candidate_atomically_restores_behavior_registry_baseline(tmp_path: Path) -> None:
    runtime = create_runtime(tmp_path / "artifact-rollback")
    try:
        store = SqliteBehaviorArtifactStore(runtime.ledger.engine)
        service = BehaviorCandidateService(
            store=store,
            clock=SystemClock(),
            ids=UuidIdGenerator(),
            catalog=FilesystemBehaviorArtifactCatalog(Path("config")),
        )
        baseline = service.ensure_baseline(
            "project:a08:rollback",
            BehaviorArtifactKind.EVALUATOR_CONTRACT,
        )
        candidate = service.create_candidate(
            project_id=baseline.project_id,
            kind=baseline.kind,
            content={**baseline.content, "version": "1.0.1", "dimensions": ["quality"]},
            parent_digest=baseline.content_digest,
        )
        entry = ImprovementRunner(store=store, clock=SystemClock()).rollback(
            baseline,
            candidate,
        )
        assert entry.active_artifact_id == baseline.artifact_id
        assert entry.active_digest == baseline.content_digest
        persisted = store.read(baseline.project_id, candidate.artifact_id)
        assert persisted is not None
        assert persisted.state == "ROLLED_BACK"
    finally:
        runtime.close()
