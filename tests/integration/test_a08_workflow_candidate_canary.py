from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue
from tests.integration.test_a02_autonomous_acquisition import request, value
from tests.integration.test_a05_bounded_recovery import RecoverySandboxAdapter, prepare_recovery

from thoth.adapters.improvement import DeterministicIndependentImprovementEvaluator
from thoth.adapters.storage import SqliteBehaviorArtifactStore
from thoth.domain.behavior_artifact import BehaviorArtifactKind
from thoth.domain.sandbox import SandboxExecutionState


@pytest.mark.asyncio
async def test_explicit_test_evaluator_simulates_workflow_pointer_transition(
    tmp_path: Path,
) -> None:
    runtime, project_id, thread_id = await prepare_recovery(
        tmp_path,
        "a08-workflow-canary",
        RecoverySandboxAdapter(
            (
                (SandboxExecutionState.FAILED, "SEMANTIC evaluator mismatch"),
                (SandboxExecutionState.FAILED, "SEMANTIC evaluator mismatch"),
            )
        ),
        max_retries=0,
        improvement_evaluator=DeterministicIndependentImprovementEvaluator(),
    )
    try:
        for ordinal in (1, 2):
            result = value(
                await runtime.bus.dispatch(
                    request(
                        "thread/input",
                        f"a08-workflow-{ordinal}",
                        {"project_id": project_id, "thread_id": thread_id},
                    )
                )
            )
        improvement = cast(dict[str, JsonValue], result["recursive_improvement"])
        assert improvement["state"] == "PROMOTED_LOCAL"
        assert improvement["behavior_artifact_kind"] == "WORKFLOW_DEFINITION"
        assert improvement["behavior_registry_active_digest"] == improvement["candidate_digest"]
        store = SqliteBehaviorArtifactStore(runtime.ledger.engine)
        registry = store.read_registry(project_id, BehaviorArtifactKind.WORKFLOW_DEFINITION)
        assert registry is not None
        assert registry.active_digest == improvement["candidate_digest"]
    finally:
        runtime.close()
