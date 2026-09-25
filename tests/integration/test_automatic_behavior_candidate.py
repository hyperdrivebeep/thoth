from pathlib import Path

from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_a05_bounded_recovery import RecoverySandboxAdapter, prepare_recovery

from thoth.adapters.evaluators.catalog import FrozenEvaluationCatalog
from thoth.adapters.storage.control_record import SqliteControlRecordStore
from thoth.adapters.storage.evaluation_run import SqliteEvaluationRunStore
from thoth.domain.evaluation_run import (
    EvaluationBinding,
    EvaluationCasePack,
    EvaluationScorer,
    sealed_payload,
)
from thoth.domain.sandbox import SandboxExecutionState


async def test_repeated_failure_generates_and_measures_one_policy_variant_without_manual_plan(
    tmp_path: Path,
) -> None:
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
                await runtime.bus.dispatch(
                    request(
                        "thread/input",
                        f"failure-{ordinal}",
                        {
                            "project_id": project,
                            "thread_id": thread,
                        },
                    )
                )
            )
        improvement = result["recursive_improvement"]
        assert improvement["state"] == "EVALUATED", improvement
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
        third = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "failure-3",
                    {
                        "project_id": project,
                        "thread_id": thread,
                    },
                )
            )
        )
        assert third["recursive_improvement"]["state"] == "HELD"
        assert (
            third["recursive_improvement"]["rollback_reason"] == "BEHAVIOR_CANDIDATE_NOT_AVAILABLE"
        )
        assert len(controls.list(project, "IMPROVEMENT", "REVISION")) == 1
    finally:
        runtime.close()
