from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import BaseModel
from tests.integration.storage_coverage_helpers import request as base_request
from tests.integration.storage_coverage_helpers import value
from tests.integration.test_a04_r2_closed_loop import A04R2Model
from tests.integration.test_a05_bounded_recovery import RecoverySandboxAdapter, prepare_recovery
from tests.integration.test_research_request_v2 import ControlledResearchModel

from thoth.adapters.evaluators.catalog import FrozenEvaluationCatalog
from thoth.adapters.storage.control_record import SqliteControlRecordStore
from thoth.adapters.storage.evaluation_run import SqliteEvaluationRunStore
from thoth.apps.runtime import AppRuntime
from thoth.domain.enums import ModelRole
from thoth.domain.evaluation_run import (
    EvaluationBinding,
    EvaluationCasePack,
    EvaluationScorer,
    sealed_payload,
)
from thoth.domain.model import ModelRequest, ModelResult
from thoth.domain.model_dispatch import CONTROLLED_MODEL_CONTROL
from thoth.domain.sandbox import SandboxExecutionState
from thoth.protocol.jsonrpc import JsonRpcRequest, JsonRpcResponse


def request(method: str, key: str, fields: dict[str, Any]) -> JsonRpcRequest:
    if method == "thread/input":
        fields = {
            **fields,
            "contract_version": 2,
            "instruction": "Apply the recorded recovery protocol and retain failure evidence.",
        }
    return base_request(method, key, fields)


async def settled(runtime: AppRuntime, req: JsonRpcRequest) -> JsonRpcResponse:
    response = await runtime.bus.dispatch(req)
    if response.error is not None:
        return response
    accepted = value(response)
    if accepted.get("status") != "ACCEPTED_RUNNING":
        return response
    await runtime.bus.drain()
    operation = runtime.bus.read_operation(str(accepted["operation_id"]))
    assert operation is not None and operation.state.value == "SUCCEEDED", operation
    return JsonRpcResponse(
        id=req.id,
        result={
            "operation_id": operation.operation_id,
            "state": operation.state.value,
            "value": cast(Any, operation.result),
        },
    )


async def test_v2_failure_observation_uses_frozen_measured_pair_and_deduplicates_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auxiliary = ControlledResearchModel()
    original = A04R2Model.structured

    async def combined(self: A04R2Model, call: ModelRequest[BaseModel]) -> ModelResult[BaseModel]:
        if call.role in {
            ModelRole.RESEARCH_PLANNER,
            ModelRole.EVIDENCE_RERANKER,
            ModelRole.SEMANTIC_REVIEWER,
            ModelRole.REVIEW_ADJUDICATOR,
            ModelRole.SOURCE_PLANNER,
            ModelRole.HYPOTHESIS_REVIEWER,
        }:
            return await auxiliary.structured(call)
        return await original(self, call)

    monkeypatch.setattr(A04R2Model, "structured", combined)
    monkeypatch.setattr(A04R2Model, "control_capability", CONTROLLED_MODEL_CONTROL, raising=False)
    scenario = "automatic-policy"
    project = "project:a05:" + scenario
    pack = EvaluationCasePack.model_validate(
        sealed_payload(
            "EVALUATION_CASE_PACK",
            "pack_digest",
            {
                "project_id": project,
                "pack_id": "repair-contract",
                "version": "1",
                "cases": [
                    {
                        "public": {"case_id": "needs-repair", "payload": {"needs_repair": True}},
                        "expected_output": {"decision": "REPAIR"},
                        "axis": "quality",
                    }
                ],
                "initial_memory": {},
                "resource_refs": (),
                "expected_visibility": "SCORER_ONLY",
                "expected_values_exposed": False,
                "max_pair_runs": 2,
            },
        )
    )
    scorer = EvaluationScorer.model_validate(
        sealed_payload(
            "EVALUATION_SCORER",
            "scorer_digest",
            {
                "scorer_id": "repair-scorer",
                "version": "1",
                "algorithm": "EXACT_JSON_V1",
            },
        )
    )
    binding = EvaluationBinding.model_validate(
        sealed_payload(
            "EVALUATION_BINDING",
            "binding_digest",
            {
                "project_id": project,
                "binding_id": "repair-binding",
                "component": "WORKFLOW_DEFINITION",
                "executor_id": "BEHAVIOR_COMPONENT_SUBPROCESS_V1",
                "case_pack": pack,
                "scorer": scorer,
                "max_requests": 4,
                "timeout_seconds": 30,
                "max_output_bytes": 262144,
            },
        )
    )
    runtime, actual_project, thread = await prepare_recovery(
        tmp_path,
        scenario,
        RecoverySandboxAdapter(
            tuple((SandboxExecutionState.FAILED, "SEMANTIC recurring failure") for _ in range(3))
        ),
        max_retries=0,
        evaluation_catalog=FrozenEvaluationCatalog((binding,)),
    )
    try:
        assert actual_project == project
        for ordinal in (1, 2):
            result = value(
                await settled(
                    runtime,
                    request(
                        "thread/input",
                        f"failure-{ordinal}",
                        {
                            "project_id": project,
                            "thread_id": thread,
                        },
                    ),
                )
            )
        improvement = result["recursive_improvement"]
        assert improvement["state"] == "EVALUATED", improvement
        assert result["improvement_observation"]["trigger_state"] == "EVALUATED"
        assert (
            result["improvement_observation"]["evaluation_run_ref"]
            == improvement["paired_evaluation_id"]
        )
        assert improvement["evaluation_provenance"] == "MEASURED_PAIR"
        pair = SqliteEvaluationRunStore(runtime.ledger.engine).read(
            project, improvement["paired_evaluation_id"]
        )
        assert pair is not None and pair.result is not None
        # The alternative disables a useful repair: actual output is worse, not fabricated success.
        assert pair.result.performance_verdict == "WORSE"
        controls = SqliteControlRecordStore(runtime.ledger.engine)
        proposals = controls.list(project, "IMPROVEMENT", "REVISION")
        assert len(proposals) == 1 and proposals[0].payload["automatic_trigger_key"]
        assert controls.list(project, "IMPROVEMENT", "EVALUATION_PLAN")[0].state == "EVALUATED"
        observations = controls.list(project, "IMPROVEMENT_RUNTIME", "FAILURE_OBSERVATION")
        assert len(observations) == 2
        assert result["improvement_observation"]["failure_observation_ref"] in {
            r.record_id for r in observations
        }
        repeated = value(
            await settled(
                runtime,
                request("thread/input", "failure-2", {"project_id": project, "thread_id": thread}),
            )
        )
        assert repeated["recursive_improvement"] == result["recursive_improvement"]
        assert len(controls.list(project, "IMPROVEMENT_RUNTIME", "FAILURE_OBSERVATION")) == 2
    finally:
        runtime.close()
