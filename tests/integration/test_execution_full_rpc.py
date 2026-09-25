from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue
from tests.integration.scoped_runtime import create_runtime
from tests.integration.test_a04_storage_authority import approve_required_roles

from thoth.domain.canonical import head_set_digest
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
async def test_execution_cancel_reconcile_retry_authorize_compensate_and_invalidate(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    inbox = workspace / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "evidence.md").write_text(
        "# Evidence\n\nThe target-state check is authoritative.\n",
        encoding="utf-8",
    )
    runtime = create_runtime(workspace)
    project_id = "project:execution-full"
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "execution-project",
                    {
                        "project_id": project_id,
                        "name": "Execution full",
                        "cutoff_at": "2026-08-31T00:00:00Z",
                    },
                )
            )
        )
        role_result = value(
            await runtime.bus.dispatch(
                request(
                    "project/role/assign",
                    "execution-role",
                    {
                        "project_id": project_id,
                        "expected_revision": 0,
                        "actor_id": "human:project-owner",
                        "role": "project-owner",
                        "scope": "PROJECT",
                        "authority_tags": ["R3_ACTION_OWNER"],
                    },
                )
            )
        )
        role_id = str(cast(dict[str, JsonValue], role_result["role"])["role_assignment_id"])
        value(
            await runtime.bus.dispatch(
                request(
                    "project/source/connect",
                    "execution-source",
                    {
                        "project_id": project_id,
                        "relative_path": "evidence.md",
                        "media_type": "text/markdown",
                        "authority": "OFFICIAL",
                        "cutoff_state": "ELIGIBLE",
                        "security_class": "INTERNAL",
                        "expected_project_revision": 1,
                    },
                )
            )
        )
        evidence = value(
            await runtime.bus.dispatch(
                request("evidence/list", "execution-evidence", {"project_id": project_id})
            )
        )
        evidence_items = cast(list[dict[str, JsonValue]], evidence["spans"])
        evidence_refs = [str(item["span_id"]) for item in evidence_items]
        evidence_digest = str(evidence_items[0]["text_sha256"])
        started_thread = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "execution-thread",
                    {
                        "project_id": project_id,
                        "thread_id": "thread:execution-full",
                        "problem": "Execute a bounded check and protected change",
                        "scope": {"workstream": "integration"},
                    },
                )
            )
        )
        object_id = str(cast(list[str], started_thread["current_object_ids"])[0])

        r0_result = value(
            await runtime.bus.dispatch(
                request(
                    "action/create",
                    "execution-r0-action",
                    {
                        "project_id": project_id,
                        "object_id": object_id,
                        "primary_purpose": "INFORMATION_ACQUISITION",
                        "specification": {
                            "description": "Read target state",
                            "expected_observation_or_change": {
                                "description": "target state is observed"
                            },
                            "effect_completeness_confirmed": True,
                            "stop_conditions": ["state read"],
                            "observability": "read receipt",
                            "effect_vector": {
                                "effect_completeness_confirmed": True,
                                "external_write": False,
                            },
                        },
                        "evidence_refs": evidence_refs,
                    },
                )
            )
        )
        r0 = cast(dict[str, JsonValue], r0_result["action"])
        r3_result = value(
            await runtime.bus.dispatch(
                request(
                    "action/create",
                    "execution-r3-action",
                    {
                        "project_id": project_id,
                        "object_id": object_id,
                        "primary_purpose": "STATE_OR_DESIGN_CHANGE",
                        "specification": {
                            "description": "Apply protected configuration change",
                            "expected_observation_or_change": {
                                "description": "configuration target changes"
                            },
                            "effect_completeness_confirmed": True,
                            "stop_conditions": ["unexpected effect"],
                            "observability": "configuration receipt",
                            "effect_vector": {
                                "effect_completeness_confirmed": True,
                                "external_write": True,
                            },
                        },
                        "evidence_refs": evidence_refs,
                    },
                )
            )
        )
        r3 = cast(dict[str, JsonValue], r3_result["action"])
        r4_result = value(
            await runtime.bus.dispatch(
                request(
                    "action/create",
                    "execution-r4-action",
                    {
                        "project_id": project_id,
                        "object_id": object_id,
                        "primary_purpose": "GOVERNANCE_ESCALATION",
                        "specification": {
                            "description": "Change official KPI",
                            "expected_observation_or_change": {"description": "KPI changes"},
                            "effect_completeness_confirmed": True,
                            "stop_conditions": ["prohibited"],
                            "observability": "none",
                            "effect_vector": {
                                "effect_completeness_confirmed": True,
                                "changes_official_kpi": True,
                            },
                        },
                        "evidence_refs": evidence_refs,
                    },
                )
            )
        )
        r4 = cast(dict[str, JsonValue], r4_result["action"])
        plan_result = value(
            await runtime.bus.dispatch(
                request(
                    "action/plan/compose",
                    "execution-plan",
                    {
                        "project_id": project_id,
                        "object_id": object_id,
                        "plan_id": "plan:execution-full",
                        "selected_action_refs": [
                            r0["action_id"],
                            r3["action_id"],
                        ],
                        "step_candidates": [
                            {
                                "step_id": "step:read",
                                "action_ref": r0["action_id"],
                                "inputs": evidence_refs,
                                "target_digests": ["target:read"],
                                "output_contract": {"type": "target-state"},
                                "preconditions": [],
                                "stop_conditions": ["state read"],
                                "effect_vector": {
                                    "effect_completeness_confirmed": True,
                                    "external_write": False,
                                },
                                "state": "READY",
                            },
                            {
                                "step_id": "step:r3",
                                "action_ref": r3["action_id"],
                                "inputs": ["step:read"],
                                "target_digests": ["baseline:configuration"],
                                "output_contract": {"type": "configuration-receipt"},
                                "preconditions": ["step:read complete"],
                                "stop_conditions": ["unexpected effect"],
                                "effect_vector": {
                                    "effect_completeness_confirmed": True,
                                    "external_write": True,
                                },
                                "state": "READY",
                            },
                            {
                                "step_id": "step:r4",
                                "action_ref": r4["action_id"],
                                "inputs": [],
                                "output_contract": {"type": "none"},
                                "preconditions": [],
                                "stop_conditions": ["prohibited"],
                                "effect_vector": {
                                    "effect_completeness_confirmed": True,
                                    "changes_official_kpi": True,
                                },
                                "state": "READY",
                            },
                        ],
                        "dependency_edges": [{"from": "step:read", "to": "step:r3"}],
                    },
                )
            )
        )
        plan = cast(dict[str, JsonValue], plan_result["plan"])
        working_head = head_set_digest(runtime.ledger.read_heads(project_id))
        start_result = value(
            await runtime.bus.dispatch(
                request(
                    "execution/start",
                    "execution-start",
                    {
                        "project_id": project_id,
                        "plan_id": plan["plan_id"],
                        "plan_revision_digest": plan["revision_digest"],
                        "execution_profile_ref": "execution:local-coordinator-v1",
                        "expected_working_head_digest": working_head,
                    },
                )
            )
        )
        execution = cast(dict[str, JsonValue], start_result["execution"])
        execution_id = str(execution["plan_execution_id"])
        attempts = cast(list[dict[str, JsonValue]], start_result["attempts"])
        assert len(attempts) == 1
        first_attempt = attempts[0]
        assert first_attempt["step_id"] == "step:read"
        assert "step:r3" in cast(dict[str, JsonValue], execution["blocked_steps"])
        assert "step:r4" in cast(list[str], execution["prohibited_steps"])

        preflight = value(
            await runtime.bus.dispatch(
                request(
                    "execution/preflight/read",
                    "execution-preflight",
                    {
                        "project_id": project_id,
                        "plan_execution_id": execution_id,
                        "step_id": "step:r3",
                    },
                )
            )
        )
        assert preflight["permitted_transition"] == "WAIT"

        paused_result = value(
            await runtime.bus.dispatch(
                request(
                    "execution/pause",
                    "execution-pause",
                    {
                        "project_id": project_id,
                        "plan_execution_id": execution_id,
                        "reason": "inspect first dispatch",
                        "expected_execution_revision": execution["revision"],
                    },
                )
            )
        )
        execution = cast(dict[str, JsonValue], paused_result["execution"])
        resumed_result = value(
            await runtime.bus.dispatch(
                request(
                    "execution/resume",
                    "execution-resume",
                    {
                        "project_id": project_id,
                        "plan_execution_id": execution_id,
                        "checkpoint_digest": execution["checkpoint_digest"],
                        "expected_execution_revision": execution["revision"],
                    },
                )
            )
        )
        execution = cast(dict[str, JsonValue], resumed_result["execution"])
        cancelled_result = value(
            await runtime.bus.dispatch(
                request(
                    "execution/cancel",
                    "execution-cancel",
                    {
                        "project_id": project_id,
                        "plan_execution_id": execution_id,
                        "scope": "step:read",
                        "reason": "exercise cancellation uncertainty",
                        "expected_execution_revision": execution["revision"],
                    },
                )
            )
        )
        execution = cast(dict[str, JsonValue], cancelled_result["execution"])
        cancelled_attempt = cast(list[dict[str, JsonValue]], cancelled_result["affected_attempts"])[
            0
        ]
        assert cancelled_attempt["state"] == "CANCEL_REQUESTED"

        not_applied_result = value(
            await runtime.bus.dispatch(
                request(
                    "execution/reconcile",
                    "execution-reconcile-not-applied",
                    {
                        "project_id": project_id,
                        "plan_execution_id": execution_id,
                        "attempt_id": cancelled_attempt["attempt_id"],
                        "target_state_evidence_refs": evidence_refs,
                        "reconciliation_method": ("TARGET_CHECK_CONFIRMED_NOT_APPLIED"),
                    },
                )
            )
        )
        execution = cast(dict[str, JsonValue], not_applied_result["execution"])
        failed_attempt = cast(dict[str, JsonValue], not_applied_result["attempt"])
        assert failed_attempt["state"] == "FAILED"
        assert not_applied_result["retry_permission"] is True
        reconciliation_id = str(
            cast(dict[str, JsonValue], not_applied_result["reconciliation"])["reconciliation_id"]
        )

        retry_result = value(
            await runtime.bus.dispatch(
                request(
                    "execution/retry",
                    "execution-retry",
                    {
                        "project_id": project_id,
                        "plan_execution_id": execution_id,
                        "step_id": "step:read",
                        "failed_attempt_id": failed_attempt["attempt_id"],
                        "retry_reason": "target check confirmed no application",
                        "expected_execution_revision": execution["revision"],
                    },
                )
            )
        )
        execution = cast(dict[str, JsonValue], retry_result["execution"])
        retry_attempt = cast(dict[str, JsonValue], retry_result["attempt"])
        assert retry_attempt["attempt_number"] == 2

        applied_result = value(
            await runtime.bus.dispatch(
                request(
                    "execution/reconcile",
                    "execution-reconcile-applied",
                    {
                        "project_id": project_id,
                        "plan_execution_id": execution_id,
                        "attempt_id": retry_attempt["attempt_id"],
                        "target_state_evidence_refs": evidence_refs,
                        "reconciliation_method": "TARGET_CHECK_CONFIRMED_APPLIED",
                    },
                )
            )
        )
        execution = cast(dict[str, JsonValue], applied_result["execution"])
        succeeded_attempt = cast(dict[str, JsonValue], applied_result["attempt"])
        assert succeeded_attempt["state"] == "SUCCEEDED"
        assert "step:r3" in cast(list[str], execution["protected_steps"])

        observation_result = value(
            await runtime.bus.dispatch(
                request(
                    "execution/observation/link",
                    "execution-observation",
                    {
                        "project_id": project_id,
                        "attempt_id": succeeded_attempt["attempt_id"],
                        "observation_refs": evidence_refs,
                        "completeness": "COMPLETE",
                        "evidence_refs": evidence_refs,
                        "expected_execution_revision": execution["revision"],
                    },
                )
            )
        )
        execution = cast(dict[str, JsonValue], observation_result["execution"])
        assert observation_result["downstream_outcome_readiness"] == "READY"

        prepared_result = value(
            await runtime.bus.dispatch(
                request(
                    "action/authorization/prepare",
                    "execution-auth-prepare",
                    {
                        "project_id": project_id,
                        "plan_id": plan["plan_id"],
                        "step_id": "step:r3",
                        "plan_revision_digest": plan["revision_digest"],
                        "predecessor_output_digests": [evidence_digest],
                        "target_baseline_digests": ["b" * 64],
                        "policy_version": "policy:current",
                    },
                )
            )
        )
        authorization = cast(dict[str, JsonValue], prepared_result["authorization"])
        decided_result = value(
            await runtime.bus.dispatch(
                request(
                    "action/authorization/decide",
                    "execution-auth-decide",
                    {
                        "project_id": project_id,
                        "authorization_id": authorization["authorization_id"],
                        "decision": "APPROVE",
                        "actor_ref": "human:project-owner",
                        "role_assignment_ref": role_id,
                        "approved_digest": authorization["exact_scope_digest"],
                    },
                )
            )
        )
        decided_result["authorization"] = cast(
            JsonValue,
            await approve_required_roles(
                runtime,
                project_id,
                cast(dict[str, JsonValue], decided_result["authorization"]),
                "human:project-owner",
                "execution-additional-roles",
            ),
        )
        assert cast(dict[str, JsonValue], decided_result["authorization"])["state"] == "APPROVED"

        resumed_r3_result = value(
            await runtime.bus.dispatch(
                request(
                    "execution/resume",
                    "execution-resume-r3",
                    {
                        "project_id": project_id,
                        "plan_execution_id": execution_id,
                        "checkpoint_digest": execution["checkpoint_digest"],
                        "expected_execution_revision": execution["revision"],
                    },
                )
            )
        )
        execution = cast(dict[str, JsonValue], resumed_r3_result["execution"])
        r3_attempts = cast(list[dict[str, JsonValue]], resumed_r3_result["new_attempts"])
        assert len(r3_attempts) == 1
        r3_attempt = r3_attempts[0]
        assert r3_attempt["authorization_digest"] == authorization["exact_scope_digest"]

        cancel_r3_result = value(
            await runtime.bus.dispatch(
                request(
                    "execution/cancel",
                    "execution-cancel-r3",
                    {
                        "project_id": project_id,
                        "plan_execution_id": execution_id,
                        "scope": "step:r3",
                        "reason": "unexpected external effect",
                        "expected_execution_revision": execution["revision"],
                    },
                )
            )
        )
        execution = cast(dict[str, JsonValue], cancel_r3_result["execution"])
        r3_cancelled = next(
            item
            for item in cast(list[dict[str, JsonValue]], cancel_r3_result["affected_attempts"])
            if item["step_id"] == "step:r3"
        )
        partial_result = value(
            await runtime.bus.dispatch(
                request(
                    "execution/reconcile",
                    "execution-reconcile-partial",
                    {
                        "project_id": project_id,
                        "plan_execution_id": execution_id,
                        "attempt_id": r3_cancelled["attempt_id"],
                        "target_state_evidence_refs": evidence_refs,
                        "reconciliation_method": "TARGET_CHECK_PARTIAL",
                    },
                )
            )
        )
        execution = cast(dict[str, JsonValue], partial_result["execution"])
        partial_attempt = cast(dict[str, JsonValue], partial_result["attempt"])
        assert partial_attempt["state"] == "PARTIAL"

        compensation_result = value(
            await runtime.bus.dispatch(
                request(
                    "execution/compensation/propose",
                    "execution-compensation",
                    {
                        "project_id": project_id,
                        "attempt_id": partial_attempt["attempt_id"],
                        "effect_refs": evidence_refs,
                        "reason": "mitigate the partial external effect",
                    },
                )
            )
        )
        execution = cast(dict[str, JsonValue], compensation_result["execution"])
        assert execution["state"] == "COMPENSATION_PENDING"
        assert compensation_result["compensation_action_ref"]

        invalidated_result = value(
            await runtime.bus.dispatch(
                request(
                    "execution/invalidate",
                    "execution-invalidate",
                    {
                        "project_id": project_id,
                        "plan_execution_id": execution_id,
                        "cause_revision_ref": "revision:new-plan",
                        "impact_refs": ["OUTCOME:stale", "HYPOTHESIS:stale"],
                    },
                )
            )
        )
        execution = cast(dict[str, JsonValue], invalidated_result["execution"])
        assert execution["state"] == "INVALIDATED"

        for method, key, params, field in (
            (
                "execution/list",
                "execution-list",
                {"project_id": project_id, "plan_id": plan["plan_id"]},
                "executions",
            ),
            (
                "execution/attempt/list",
                "execution-attempt-list",
                {"project_id": project_id, "plan_execution_id": execution_id},
                "attempts",
            ),
        ):
            result = value(
                await runtime.bus.dispatch(request(method, key, cast(dict[str, object], params)))
            )
            assert cast(list[object], result[field])
        assert isinstance(
            value(
                await runtime.bus.dispatch(
                    request(
                        "execution/read",
                        "execution-read",
                        {"project_id": project_id, "plan_execution_id": execution_id},
                    )
                )
            )["execution"],
            dict,
        )
        assert isinstance(
            value(
                await runtime.bus.dispatch(
                    request(
                        "execution/attempt/read",
                        "execution-attempt-read",
                        {
                            "project_id": project_id,
                            "attempt_id": partial_attempt["attempt_id"],
                        },
                    )
                )
            )["attempt"],
            dict,
        )
        frontier = value(
            await runtime.bus.dispatch(
                request(
                    "execution/frontier/read",
                    "execution-frontier",
                    {"project_id": project_id, "plan_execution_id": execution_id},
                )
            )
        )
        assert "step:r4" in cast(list[str], frontier["prohibited"])
        effects = value(
            await runtime.bus.dispatch(
                request(
                    "execution/effect/read",
                    "execution-effect",
                    {
                        "project_id": project_id,
                        "attempt_id": partial_attempt["attempt_id"],
                    },
                )
            )
        )
        assert cast(list[object], effects["effects"])
        assert isinstance(
            value(
                await runtime.bus.dispatch(
                    request(
                        "execution/reconciliation/read",
                        "execution-reconciliation-read",
                        {
                            "project_id": project_id,
                            "reconciliation_id": reconciliation_id,
                        },
                    )
                )
            )["reconciliation"],
            dict,
        )
        audit = value(
            await runtime.bus.dispatch(
                request(
                    "execution/audit/read",
                    "execution-audit",
                    {"project_id": project_id, "plan_execution_id": execution_id},
                )
            )
        )
        assert len(cast(list[object], audit["records"])) >= 12
    finally:
        runtime.close()
