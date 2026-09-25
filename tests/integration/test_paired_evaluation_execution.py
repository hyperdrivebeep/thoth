"""N05: ordinary Thread entry must not manufacture improvement evidence."""

import json
from pathlib import Path

from tests.integration.paired_evaluation_helpers import pair_harness
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_a02_autonomous_acquisition import StaticModelResolver
from tests.integration.test_a04_r2_closed_loop import A04R2Model
from tests.integration.test_a05_bounded_recovery import (
    RecoverySandboxAdapter,
    prepare_recovery,
    recovery_policy,
)

from thoth.adapters.storage import ContentAddressedObjectStore
from thoth.adapters.storage.behavior_artifact import SqliteBehaviorArtifactStore
from thoth.adapters.storage.control_record import SqliteControlRecordStore
from thoth.domain.behavior_artifact import BehaviorArtifactKind
from thoth.domain.sandbox import SandboxExecutionState


async def test_normal_repeated_failure_holds_without_a_configured_real_evaluator(
    tmp_path: Path,
) -> None:
    runtime, project, thread = await prepare_recovery(
        tmp_path,
        "n05-no-evaluator",
        RecoverySandboxAdapter(
            (
                (SandboxExecutionState.FAILED, "SEMANTIC repeated evaluation requirement"),
                (SandboxExecutionState.FAILED, "SEMANTIC repeated evaluation requirement"),
            )
        ),
        max_retries=0,
    )
    try:
        result = {}
        for ordinal in range(2):
            result = value(
                await runtime.bus.dispatch(
                    request(
                        "thread/input",
                        f"n05-no-evaluator-{ordinal}",
                        {"project_id": project, "thread_id": thread},
                    )
                )
            )
        improvement = result["recursive_improvement"]
        assert improvement["state"] == "HELD", improvement
        assert improvement["rollback_reason"] == "EVALUATOR_NOT_CONFIGURED"
        assert improvement["evaluation"] is None
        assert improvement["exposures"] == []
        assert improvement["exact_digest_canary"] is False
        assert (
            SqliteBehaviorArtifactStore(runtime.ledger.engine).read_registry(
                project, BehaviorArtifactKind.WORKFLOW_DEFINITION
            )
            is None
        )
        records = SqliteControlRecordStore(runtime.ledger.engine)
        assert len(records.list(project, "IMPROVEMENT_RUNTIME", "FAILURE_OBSERVATION")) == 2
        requests = records.list(project, "IMPROVEMENT_RUNTIME", "EVALUATION_REQUEST")
        assert len(requests) == 1 and requests[0].state == "HELD"
    finally:
        runtime.close()


async def test_public_pair_executes_distinct_artifacts_then_scores_real_outputs(
    tmp_path: Path,
) -> None:
    baseline = {"op": "input", "key": "x"}
    candidate = {"op": "add", "args": [{"op": "input", "key": "x"}, {"op": "literal", "value": 1}]}
    cases = [
        {
            "public": {"case_id": "one", "payload": {"x": 2}},
            "expected_output": {"answer": 3},
            "axis": "quality",
        }
    ]
    async with pair_harness(tmp_path / "pair", baseline, candidate, cases) as h:
        response = await h.run()
        assert response["state"] == "COMPLETE", response
        result = response["pair"]["result"]
        assert result["performance_verdict"] == "BETTER"
        assert result["baseline_metrics"]["quality_bps"] == 0
        assert result["candidate_metrics"]["quality_bps"] == 10000
        assert result["baseline"]["workspace_id"] != result["candidate"]["workspace_id"]
        assert result["baseline"]["execution_id"] != result["candidate"]["execution_id"]
        objects = ContentAddressedObjectStore(tmp_path / "pair")
        observed = json.loads(objects.read(result["candidate"]["output_blob_digest"]))
        assert observed["outputs"] == [{"answer": 3}]
        assert result["promotion_eligible"] is False
        assert result["exposure_stage"] == "OFFLINE"
        assert (
            SqliteBehaviorArtifactStore(h.runtime.ledger.engine).read_registry(
                h.project, BehaviorArtifactKind.WORKFLOW_DEFINITION
            )
            is None
        )
        repeated = await h.run("same-pair-new-request")
        assert repeated["pair"]["result"] == result
        assessed = value(
            await h.runtime.bus.dispatch(
                request(
                    "improvement/evaluation/assess",
                    "assess-real-pair",
                    {
                        "project_id": h.project,
                        "evaluation_plan_id": h.plan["record_id"],
                        "expected_evaluation_revision": h.plan["version"],
                        "result_refs": [result["pair_id"]],
                        "evaluator_result_refs": [result["result_digest"]],
                        "exposure_ledger_ref": result["exposure_id"],
                    },
                )
            )
        )
        assert assessed["evaluation_validity"] == "VALID"
        assert assessed["performance_verdict"] == "BETTER"
        assert assessed["promotion_eligibility"] is False


async def test_normal_thread_invokes_its_explicit_runnable_plan_without_activation(
    tmp_path: Path,
) -> None:
    thread_id = "thread:n05-normal-pair"
    baseline = {"op": "input", "key": "x"}
    candidate = {"op": "add", "args": [{"op": "input", "key": "x"}, {"op": "literal", "value": 1}]}
    cases = [
        {
            "public": {"case_id": "one", "payload": {"x": 2}},
            "expected_output": {"answer": 3},
            "axis": "quality",
        }
    ]
    sandbox = RecoverySandboxAdapter(
        (
            (SandboxExecutionState.FAILED, "SEMANTIC repeated n05 trigger"),
            (SandboxExecutionState.FAILED, "SEMANTIC repeated n05 trigger"),
        )
    )
    workspace = tmp_path / "normal-pair"
    async with pair_harness(
        workspace,
        baseline,
        candidate,
        cases,
        thread_id=thread_id,
        runtime_options={
            "model_resolver": StaticModelResolver(A04R2Model()),
            "sandbox_adapter": sandbox,
        },
    ) as h:
        project = value(
            await h.runtime.bus.dispatch(
                request("project/read", "normal-project", {"project_id": h.project})
            )
        )
        policy = recovery_policy(0)
        policy["connector_allowlist"] = ["local-file-upload"]
        value(
            await h.runtime.bus.dispatch(
                request(
                    "project/policy/update",
                    "normal-policy",
                    {
                        "project_id": h.project,
                        "expected_revision": project["revision"],
                        "payload": policy,
                    },
                )
            )
        )
        inbox = workspace / "inbox"
        inbox.mkdir(exist_ok=True)
        (inbox / "basis.md").write_text("# Basis\n\nRun a bounded comparison.\n", encoding="utf-8")
        value(
            await h.runtime.bus.dispatch(
                request(
                    "project/source/connect",
                    "normal-source",
                    {
                        "project_id": h.project,
                        "relative_path": "basis.md",
                        "media_type": "text/markdown",
                        "authority": "OFFICIAL",
                        "cutoff_state": "ELIGIBLE",
                        "security_class": "INTERNAL",
                    },
                )
            )
        )
        output = {}
        for index in range(2):
            output = value(
                await h.runtime.bus.dispatch(
                    request(
                        "thread/input",
                        f"normal-input-{index}",
                        {
                            "project_id": h.project,
                            "thread_id": thread_id,
                        },
                    )
                )
            )
        improvement = output["recursive_improvement"]
        assert improvement["state"] == "EVALUATED", improvement
        assert improvement["evaluation_provenance"] == "MEASURED_PAIR"
        assert improvement["paired_evaluation_id"]
        assert improvement["exposures"] == [] and improvement["exact_digest_canary"] is False
        pair = value(
            await h.runtime.bus.dispatch(
                request(
                    "improvement/evaluation/result/read",
                    "normal-pair-read",
                    {
                        "project_id": h.project,
                        "pair_id": improvement["paired_evaluation_id"],
                    },
                )
            )
        )["pair"]
        assert pair["result"]["performance_verdict"] == "BETTER"
        assert (
            SqliteBehaviorArtifactStore(h.runtime.ledger.engine).read_registry(
                h.project, BehaviorArtifactKind.WORKFLOW_DEFINITION
            )
            is None
        )
