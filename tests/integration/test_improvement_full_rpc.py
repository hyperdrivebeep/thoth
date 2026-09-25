from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

from thoth.apps.runtime import create_runtime
from thoth.protocol.jsonrpc import JsonRpcRequest, JsonRpcResponse


def request(method: str, key: str, value: dict[str, object]) -> JsonRpcRequest:
    return JsonRpcRequest.model_validate(
        {
            "id": key,
            "method": method,
            "params": {"_meta": {"idempotencyKey": key}, "input": value},
        }
    )


def value(response: JsonRpcResponse) -> dict[str, JsonValue]:
    assert response.error is None
    assert response.result is not None
    child = response.result["value"]
    assert isinstance(child, dict)
    return cast(dict[str, JsonValue], child)


@pytest.mark.asyncio
async def test_improvement_unresolved_evaluation_preserves_draft_lifecycle_and_retirement(
    tmp_path: Path,
) -> None:
    runtime = create_runtime(tmp_path / "workspace")
    project_id = "project:improvement-full"
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "improvement-project",
                    {
                        "project_id": project_id,
                        "name": "Improvement full",
                        "cutoff_at": "2026-08-31T00:00:00Z",
                    },
                )
            )
        )
        role_result = value(
            await runtime.bus.dispatch(
                request(
                    "project/role/assign",
                    "improvement-role",
                    {
                        "project_id": project_id,
                        "expected_revision": 0,
                        "actor_id": "human:improvement-owner",
                        "role": "improvement-owner",
                        "scope": "PROJECT",
                        "authority_tags": ["IMPROVEMENT_OWNER"],
                    },
                )
            )
        )
        role_id = str(cast(dict[str, JsonValue], role_result["role"])["role_assignment_id"])

        prohibited = await runtime.bus.dispatch(
            request(
                "improvement/propose",
                "improvement-prohibited",
                {
                    "project_id": project_id,
                    "target_component": "PRODUCTION_MODEL_WEIGHTS",
                    "scope_key": "agent:production",
                    "trigger_refs": [],
                    "baseline_digest": "a" * 64,
                    "candidate_content": {"weights": "replace"},
                    "improvement_hypothesis": "self-replace weights",
                    "evaluation_contract_ref": "evaluation:1",
                },
            )
        )
        assert prohibited.error is not None

        proposed_result = value(
            await runtime.bus.dispatch(
                request(
                    "improvement/propose",
                    "improvement-propose",
                    {
                        "project_id": project_id,
                        "target_component": "PROMPT",
                        "scope_key": "hypothesis-generator:integration",
                        "trigger_refs": ["outcome:limited"],
                        "baseline_digest": "a" * 64,
                        "candidate_content": {
                            "prompt_patch": "require explicit counterevidence query"
                        },
                        "improvement_hypothesis": (
                            "Explicit counterevidence instructions reduce unsupported candidates"
                        ),
                        "evaluation_contract_ref": "evaluation:hypothesis-quality-v1",
                    },
                )
            )
        )
        improvement = cast(dict[str, JsonValue], proposed_result["improvement"])
        improvement_id = str(improvement["record_id"])
        payload = cast(dict[str, JsonValue], improvement["payload"])
        candidate_digest = str(payload["candidate_digest"])
        assert improvement["state"] == "PROPOSED"

        revised_result = value(
            await runtime.bus.dispatch(
                request(
                    "improvement/revise",
                    "improvement-revise",
                    {
                        "project_id": project_id,
                        "improvement_revision_id": improvement_id,
                        "patch": {
                            "improvement_hypothesis": (
                                "Counterevidence instructions improve unsupported-rate guardrail"
                            )
                        },
                        "evidence_refs": ["research:counterevidence"],
                        "reason": "clarify the testable behavior hypothesis",
                        "expected_revision_digest": improvement["record_digest"],
                    },
                )
            )
        )
        improvement = cast(dict[str, JsonValue], revised_result["improvement"])

        evaluation_result = value(
            await runtime.bus.dispatch(
                request(
                    "improvement/evaluation/plan",
                    "improvement-evaluation-plan",
                    {
                        "project_id": project_id,
                        "improvement_revision_id": improvement_id,
                        "baseline_digest": "a" * 64,
                        "datasets": [
                            {
                                "dataset_ref": "fixture:hidden-holdout",
                                "contains_expected_values": False,
                            }
                        ],
                        "evaluator_refs": ["evaluator:frozen-external-v1"],
                        "guardrails": [
                            {
                                "name": "unsupported rate",
                                "critical": True,
                                "status": "PASS",
                            },
                            {
                                "name": "latency",
                                "critical": False,
                                "status": "PASS",
                            },
                        ],
                        "exposure_policy_ref": "exposure:hidden-v1",
                    },
                )
            )
        )
        evaluation = cast(dict[str, JsonValue], evaluation_result["evaluation_plan"])
        evaluation_id = str(evaluation["record_id"])
        assert evaluation["state"] == "PLANNED"
        assessed_result = value(
            await runtime.bus.dispatch(
                request(
                    "improvement/evaluation/assess",
                    "improvement-evaluation-assess",
                    {
                        "project_id": project_id,
                        "evaluation_plan_id": evaluation_id,
                        "result_refs": ["result:candidate", "result:baseline"],
                        "evaluator_result_refs": ["eval:frozen:1"],
                        "exposure_ledger_ref": "exposure-ledger:1",
                        "expected_evaluation_revision": evaluation["version"],
                    },
                )
            )
        )
        assert assessed_result["evaluation_validity"] == "INCONCLUSIVE"
        assert assessed_result["performance_verdict"] == "INCONCLUSIVE"
        assert assessed_result["promotion_eligibility"] is False

        offline = value(
            await runtime.bus.dispatch(
                request(
                    "improvement/exposure/prepare",
                    "improvement-exposure-offline",
                    {
                        "project_id": project_id,
                        "improvement_revision_id": improvement_id,
                        "requested_exposure_state": "OFFLINE",
                        "evaluation_plan_id": evaluation_id,
                        "scope": {"environment": "offline"},
                        "duration_budget": {"requests": 100},
                        "stop_rollback_contract": {
                            "stop": "guardrail failure",
                            "rollback": "baseline digest",
                        },
                    },
                )
            )
        )
        offline_id = str(cast(dict[str, JsonValue], offline["exposure"])["record_id"])
        shadow = value(
            await runtime.bus.dispatch(
                request(
                    "improvement/exposure/prepare",
                    "improvement-exposure-shadow",
                    {
                        "project_id": project_id,
                        "improvement_revision_id": improvement_id,
                        "requested_exposure_state": "SHADOW",
                        "evaluation_plan_id": evaluation_id,
                        "scope": {"environment": "shadow"},
                        "duration_budget": {"requests": 20},
                        "stop_rollback_contract": {
                            "stop": "guardrail failure",
                            "rollback": "baseline digest",
                        },
                    },
                )
            )
        )
        shadow_id = str(cast(dict[str, JsonValue], shadow["exposure"])["record_id"])
        canary = value(
            await runtime.bus.dispatch(
                request(
                    "improvement/exposure/prepare",
                    "improvement-exposure-canary",
                    {
                        "project_id": project_id,
                        "improvement_revision_id": improvement_id,
                        "requested_exposure_state": "CANARY",
                        "evaluation_plan_id": evaluation_id,
                        "scope": {"environment": "canary"},
                        "duration_budget": {"requests": 5},
                        "stop_rollback_contract": {
                            "stop": "any critical guardrail failure",
                            "rollback": "baseline digest",
                        },
                    },
                )
            )
        )
        canary_value = cast(dict[str, JsonValue], canary["exposure"])
        canary_id = str(canary_value["record_id"])
        canary_payload = cast(dict[str, JsonValue], canary_value["payload"])
        assert canary_payload["authorization_required"] is True

        promotion_result = value(
            await runtime.bus.dispatch(
                request(
                    "improvement/promotion/prepare",
                    "improvement-promotion-prepare",
                    {
                        "project_id": project_id,
                        "improvement_revision_id": improvement_id,
                        "assessment_refs": [evaluation_id],
                        "candidate_digest": candidate_digest,
                        "baseline_digest": "a" * 64,
                        "target_scope": "hypothesis-generator:integration",
                        "policy_version": "promotion:1",
                    },
                )
            )
        )
        promotion = cast(dict[str, JsonValue], promotion_result["promotion"])
        assert promotion["state"] == "NOT_ELIGIBLE"
        promotion_id = str(promotion["record_id"])
        denied = await runtime.bus.dispatch(
            request(
                "improvement/promotion/decide",
                "improvement-promotion-decide",
                {
                    "project_id": project_id,
                    "promotion_candidate_id": promotion_id,
                    "decision": "APPROVE",
                    "actor_ref": "human:improvement-owner",
                    "role_assignment_ref": role_id,
                    "approved_digest": promotion["record_digest"],
                    "reason": "independent gates passed",
                },
            )
        )
        assert denied.error is not None
        assert denied.error.code == -32030

        rollback = value(
            await runtime.bus.dispatch(
                request(
                    "improvement/rollback/prepare",
                    "improvement-rollback",
                    {
                        "project_id": project_id,
                        "promoted_revision_ref": improvement_id,
                        "guardrail_failure_refs": ["guardrail:latency-regression"],
                        "rollback_target_digest": "a" * 64,
                        "target_scope": "hypothesis-generator:integration",
                    },
                )
            )
        )
        assert rollback["residual_state"] == "REQUIRES_REASSESSMENT"
        retired = value(
            await runtime.bus.dispatch(
                request(
                    "improvement/retire/propose",
                    "improvement-retire",
                    {
                        "project_id": project_id,
                        "improvement_revision_id": improvement_id,
                        "reason": "superseded by a later candidate",
                        "evidence_refs": [],
                        "expected_revision_digest": improvement["record_digest"],
                    },
                )
            )
        )
        assert retired["history_preserved"] is True

        assert cast(
            list[object],
            value(
                await runtime.bus.dispatch(
                    request(
                        "improvement/list",
                        "improvement-list",
                        {"project_id": project_id},
                    )
                )
            )["improvements"],
        )
        for method, key, record_id, field in (
            ("improvement/read", "improvement-read", improvement_id, "improvement"),
            (
                "improvement/evaluation/read",
                "improvement-evaluation-read",
                evaluation_id,
                "evaluation",
            ),
            (
                "improvement/exposure/read",
                "improvement-exposure-read",
                offline_id,
                "exposure",
            ),
            (
                "improvement/shadow/read",
                "improvement-shadow-read",
                shadow_id,
                "shadow",
            ),
            (
                "improvement/canary/read",
                "improvement-canary-read",
                canary_id,
                "canary",
            ),
            (
                "improvement/promotion/read",
                "improvement-promotion-read",
                promotion_id,
                "promotion",
            ),
        ):
            params: dict[str, object] = {"project_id": project_id}
            if method == "improvement/read":
                params["improvement_revision_id"] = record_id
            else:
                params["record_id"] = record_id
            result = value(await runtime.bus.dispatch(request(method, key, params)))
            assert isinstance(result[field], dict)
        audit = value(
            await runtime.bus.dispatch(
                request(
                    "improvement/audit/read",
                    "improvement-audit",
                    {
                        "project_id": project_id,
                        "improvement_revision_id": improvement_id,
                    },
                )
            )
        )
        assert cast(list[object], audit["records"])
    finally:
        runtime.close()
