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
async def test_hidden_holdout_leak_is_a_typed_rollback_without_canary(tmp_path: Path) -> None:
    runtime, project_id, thread_id = await prepare_recovery(
        tmp_path,
        "a08-holdout-ledger",
        RecoverySandboxAdapter(
            (
                (SandboxExecutionState.FAILED, "SEMANTIC holdout boundary"),
                (SandboxExecutionState.FAILED, "SEMANTIC holdout boundary"),
            )
        ),
        max_retries=0,
        improvement_evaluator=DeterministicIndependentImprovementEvaluator(
            {"hidden_holdout_exposed": True}
        ),
    )
    try:
        for ordinal in (1, 2):
            result = value(
                await runtime.bus.dispatch(
                    request(
                        "thread/input",
                        f"a08-holdout-{ordinal}",
                        {"project_id": project_id, "thread_id": thread_id},
                    )
                )
            )
        improvement = cast(dict[str, JsonValue], result["recursive_improvement"])
        assert improvement["state"] == "ROLLED_BACK"
        assert improvement["rollback_reason"] == "HOLDOUT_LEAK"
        assert improvement["hidden_holdout_exposed"] is True
        assert improvement["exact_digest_canary"] is False
        assert [
            item["stage"] for item in cast(list[dict[str, JsonValue]], improvement["exposures"])
        ] == ["OFFLINE"]
        registry = SqliteBehaviorArtifactStore(runtime.ledger.engine).read_registry(
            project_id,
            BehaviorArtifactKind.WORKFLOW_DEFINITION,
        )
        assert registry is not None
        assert improvement["behavior_registry_active_digest"] == registry.baseline_digest
    finally:
        runtime.close()
