"""Actual policy evidence and exact approval precede an armed canary."""

from pathlib import Path

from tests.integration.paired_evaluation_helpers import pair_harness
from tests.integration.storage_coverage_helpers import request, value

from thoth.adapters.storage.behavior_execution import SqliteBehaviorExecutionStore


async def test_offline_evidence_cannot_arm_canary_without_exact_human_decision(
    tmp_path: Path,
) -> None:
    policies = (
        {"kind": "WORKFLOW_DEFINITION", "version": "2.0.0", "max_semantic_repairs": 0},
        {"kind": "WORKFLOW_DEFINITION", "version": "2.0.0", "max_semantic_repairs": 1},
    )
    cases: list[dict[str, object]] = [
        {
            "public": {"case_id": "repair", "payload": {"needs_repair": True}},
            "expected_output": {"decision": "REPAIR"},
            "axis": "quality",
        }
    ]
    async with pair_harness(
        tmp_path,
        {},
        {},
        cases,
        policy_pair=policies,
        executor_id="BEHAVIOR_COMPONENT_SUBPROCESS_V1",
    ) as h:
        project = value(
            await h.runtime.bus.dispatch(
                request(
                    "project/read",
                    "read-project",
                    {
                        "project_id": h.project,
                    },
                )
            )
        )
        role = value(
            await h.runtime.bus.dispatch(
                request(
                    "project/role/assign",
                    "improvement-role",
                    {
                        "project_id": h.project,
                        "expected_revision": project["revision"],
                        "actor_id": "human:local-improvement-owner",
                        "role": "improvement-owner",
                        "scope": "PROJECT",
                        "authority_tags": ["CAP_READ", "CAP_WRITE"],
                    },
                )
            )
        )
        pair = (await h.run())["pair"]["result"]
        value(
            await h.runtime.bus.dispatch(
                request(
                    "improvement/evaluation/assess",
                    "assess-policy",
                    {
                        "project_id": h.project,
                        "evaluation_plan_id": h.plan["record_id"],
                        "expected_evaluation_revision": 1,
                        "result_refs": [pair["pair_id"]],
                        "evaluator_result_refs": [pair["result_digest"]],
                        "exposure_ledger_ref": pair["exposure_id"],
                    },
                )
            )
        )
        prepared = value(
            await h.runtime.bus.dispatch(
                request(
                    "improvement/exposure/prepare",
                    "prepare-canary",
                    {
                        "project_id": h.project,
                        "improvement_revision_id": h.proposal["record_id"],
                        "requested_exposure_state": "CANARY",
                        "evaluation_plan_id": h.plan["record_id"],
                        "scope": {"environment": "LOCAL"},
                        "duration_budget": {
                            "duration_seconds": 60,
                            "max_requests": 2,
                            "max_model_calls": 8,
                            "max_billed_cost_microunits": 0,
                        },
                        "stop_rollback_contract": {
                            "rollback_target_digest": h.plan["payload"]["baseline_digest"]
                        },
                    },
                )
            )
        )
        exposure = prepared["runtime_exposure"]
        identifier = exposure["spec"]["exposure_id"]
        assert exposure["state"] == "PREPARED" and not prepared["actual_execution_observed"]
        assert exposure["stage_evidence"][0]["status"] == "EXECUTED"
        assert exposure["stage_evidence"][1]["status"] == "SKIPPED"
        stages = {item["stage"]: item for item in exposure["stage_evidence"]}
        assert set(stages) == {"OFFLINE", "SANDBOX", "SHADOW", "CANARY"}
        assert stages["CANARY"]["status"] == "PENDING"
        assert stages["SHADOW"]["status"] == "SKIPPED"
        assert stages["SHADOW"]["reason_code"] == "POLICY_ONLY_COMPARISON_NO_MODEL_QUALITY_CLAIM"
        assert not stages["SHADOW"]["execution_refs"]
        denied = await h.runtime.bus.dispatch(
            request(
                "improvement/exposure/start",
                "unapproved-start",
                {
                    "project_id": h.project,
                    "exposure_id": identifier,
                    "expected_revision": 1,
                },
            )
        )
        assert denied.error is not None and denied.error.data is not None
        assert denied.error.data["reason_code"] == "BEHAVIOR_EXACT_APPROVAL_REQUIRED"
        store = SqliteBehaviorExecutionStore(h.runtime.ledger.engine)
        stored = store.read(h.project, identifier)
        assert stored is not None and stored.state == "PREPARED" and stored.observations == ()
        role_ref = role["role"]["role_assignment_id"]
        approved = value(
            await h.runtime.bus.dispatch(
                request(
                    "improvement/exposure/decide",
                    "approve-canary",
                    {
                        "project_id": h.project,
                        "exposure_id": identifier,
                        "expected_revision": 1,
                        "approved_digest": exposure["spec"]["spec_digest"],
                        "decision": "APPROVE",
                        "actor_ref": "human:local-improvement-owner",
                        "role_assignment_ref": role_ref,
                    },
                )
            )
        )
        assert (
            approved["exposure"]["state"] == "APPROVED"
            and not approved["actual_execution_observed"]
        )
        armed = value(
            await h.runtime.bus.dispatch(
                request(
                    "improvement/exposure/start",
                    "approved-start",
                    {
                        "project_id": h.project,
                        "exposure_id": identifier,
                        "expected_revision": 2,
                    },
                )
            )
        )
        assert armed["exposure"]["state"] == "ARMED" and not armed["actual_execution_observed"]
        assert armed["exposure"]["request_count"] == 0
