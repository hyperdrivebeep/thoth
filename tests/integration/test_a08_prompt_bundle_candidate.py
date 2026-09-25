from __future__ import annotations

from pathlib import Path

import pytest

from thoth.adapters.behavior_catalog import FilesystemBehaviorArtifactCatalog
from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.storage import SqliteBehaviorArtifactStore
from thoth.application.services.behavior_candidate_service import BehaviorCandidateService
from thoth.application.services.improvement_runner import ImprovementRunner
from thoth.apps.runtime import create_runtime
from thoth.domain.behavior_artifact import BehaviorArtifactKind, BehaviorArtifactState


def test_behavior_artifact_contract_separates_baseline_candidate_and_active() -> None:
    assert BehaviorArtifactKind.PROMPT_BUNDLE.value == "PROMPT_BUNDLE"
    assert BehaviorArtifactKind.RETRIEVAL_POLICY.value == "RETRIEVAL_POLICY"
    assert BehaviorArtifactKind.WORKFLOW_DEFINITION.value == "WORKFLOW_DEFINITION"
    assert BehaviorArtifactKind.EVALUATOR_CONTRACT.value == "EVALUATOR_CONTRACT"
    assert BehaviorArtifactState.BASELINE.value == "BASELINE"
    assert BehaviorArtifactState.CANDIDATE.value == "CANDIDATE"
    assert BehaviorArtifactState.ACTIVE.value == "ACTIVE"
    assert BehaviorArtifactState.ROLLED_BACK.value == "ROLLED_BACK"


def test_versioned_prompt_candidate_can_promote_without_code_or_weight_change(
    tmp_path: Path,
) -> None:
    runtime = create_runtime(tmp_path / "prompt-candidate")
    try:
        store = SqliteBehaviorArtifactStore(runtime.ledger.engine)
        service = BehaviorCandidateService(
            store=store,
            clock=SystemClock(),
            ids=UuidIdGenerator(),
            catalog=FilesystemBehaviorArtifactCatalog(Path("config")),
        )
        baseline = service.ensure_baseline("project:a08:prompt", BehaviorArtifactKind.PROMPT_BUNDLE)
        assert baseline.content["version"] == "1.0.0"
        candidate = service.create_candidate(
            project_id="project:a08:prompt",
            kind=BehaviorArtifactKind.PROMPT_BUNDLE,
            content={
                **baseline.content,
                "version": "1.0.1",
                "required_sections": ["evidence_refs"],
            },
            parent_digest=baseline.content_digest,
        )
        entry = ImprovementRunner(store=store, clock=SystemClock()).promote(
            baseline,
            candidate,
        )
        assert entry.active_digest == candidate.content_digest
        assert entry.baseline_digest == baseline.content_digest
        assert store.read("project:a08:prompt", candidate.artifact_id).state == "ACTIVE"  # type: ignore[union-attr]

        isolated = service.create_candidate(
            project_id="project:a08:prompt",
            kind=BehaviorArtifactKind.CODE_PATCH,
            content={"patch_digest": "0" * 64},
            parent_digest=baseline.content_digest,
        )
        assert isolated.promotion_eligible is False
        assert isolated.auto_apply_allowed is False
        with pytest.raises(ValueError, match="cannot be automatically promoted"):
            ImprovementRunner(store=store, clock=SystemClock()).promote(baseline, isolated)
    finally:
        runtime.close()
