from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue
from tests.integration.scoped_runtime import create_runtime
from tests.integration.test_a04_storage_authority import approve_required_roles

from thoth.domain.auth import AuthenticatedActorContext, authenticated_actor_scope
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
async def test_action_decision_plan_policy_and_content_bound_authorization(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    inbox = workspace / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "evidence.md").write_text(
        "# Evidence\n\nThe interface result is inconsistent.\n",
        encoding="utf-8",
    )
    runtime = create_runtime(workspace)
    project_id = "project:action-full"
    thread_id = "thread:action-full"
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "action-project",
                    {
                        "project_id": project_id,
                        "name": "Action full",
                        "cutoff_at": "2026-08-31T00:00:00Z",
                    },
                )
            )
        )
        role_result = value(
            await runtime.bus.dispatch(
                request(
                    "project/role/assign",
                    "action-role",
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
        role = cast(dict[str, JsonValue], role_result["role"])
        role_id = str(role["role_assignment_id"])
        value(
            await runtime.bus.dispatch(
                request(
                    "project/source/connect",
                    "action-source",
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
                request("evidence/list", "action-evidence", {"project_id": project_id})
            )
        )
        evidence_refs = [
            str(item["span_id"]) for item in cast(list[dict[str, JsonValue]], evidence["spans"])
        ]
        started = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "action-thread",
                    {
                        "project_id": project_id,
                        "thread_id": thread_id,
                        "problem": "What should we do about the inconsistent result?",
                        "scope": {"workstream": "integration"},
                    },
                )
            )
        )
        object_id = str(cast(list[str], started["current_object_ids"])[0])
        hypotheses = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/generate",
                    "action-hypotheses",
                    {
                        "project_id": project_id,
                        "object_id": object_id,
                        "portfolio_id": "portfolio:action-hypotheses",
                        "question": "Why is the result inconsistent?",
                        "evidence_scope": evidence_refs,
                        "intent_hints": ["DIAGNOSTIC_CAUSAL"],
                        "generation_policy_ref": "generation:bounded-v1",
                    },
                )
            )
        )
        hypothesis_refs = [
            str(item["hypothesis_id"])
            for item in cast(list[dict[str, JsonValue]], hypotheses["hypotheses"])
        ]

        generated_result = value(
            await runtime.bus.dispatch(
                request(
                    "action/generate",
                    "action-generate",
                    {
                        "project_id": project_id,
                        "object_id": object_id,
                        "hypothesis_refs": hypothesis_refs[:2],
                        "decision_need": "Discriminate causes with bounded information value",
                        "evidence_scope": evidence_refs,
                        "purpose_hints": ["HYPOTHESIS_DISCRIMINATION"],
                    },
                )
            )
        )
        generated = cast(list[dict[str, JsonValue]], generated_result["actions"])
        assert len(generated) == 3
        assert {str(item["risk_tier"]) for item in generated} == {"R0", "R2"}

        r3_result = value(
            await runtime.bus.dispatch(
                request(
                    "action/create",
                    "action-r3",
                    {
                        "project_id": project_id,
                        "object_id": object_id,
                        "portfolio_id": "action-portfolio:full",
                        "hypothesis_refs": hypothesis_refs[:1],
                        "primary_purpose": "STATE_OR_DESIGN_CHANGE",
                        "secondary_purposes": ["RISK_REDUCTION"],
                        "specification": {
                            "description": "Apply a controlled configuration change",
                            "expected_observation_or_change": {
                                "description": "timestamp mismatch decreases"
                            },
                            "effect_completeness_confirmed": True,
                            "stop_conditions": ["mismatch increases"],
                            "observability": "configuration and measurement receipts",
                            "effect_vector": {
                                "effect_completeness_confirmed": True,
                                "external_write": True,
                                "physical_action": False,
                                "changes_official_baseline": False,
                            },
                        },
                        "evidence_refs": evidence_refs,
                    },
                )
            )
        )
        r3 = cast(dict[str, JsonValue], r3_result["action"])
        assert r3["risk_tier"] == "R3"
        assert r3["policy_state"] == "APPROVAL_REQUIRED"

        r4_result = value(
            await runtime.bus.dispatch(
                request(
                    "action/create",
                    "action-r4",
                    {
                        "project_id": project_id,
                        "object_id": object_id,
                        "portfolio_id": "action-portfolio:full",
                        "primary_purpose": "GOVERNANCE_ESCALATION",
                        "specification": {
                            "description": "Change the official KPI automatically",
                            "expected_observation_or_change": {
                                "description": "official KPI changes"
                            },
                            "effect_completeness_confirmed": True,
                            "stop_conditions": ["always prohibited"],
                            "observability": "not executable",
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
        assert r4["risk_tier"] == "R4"
        assert r4["policy_state"] == "PROHIBITED"

        revised_r3_result = value(
            await runtime.bus.dispatch(
                request(
                    "action/revise",
                    "action-r3-revise",
                    {
                        "project_id": project_id,
                        "action_id": r3["action_id"],
                        "expected_revision_digest": r3["revision_digest"],
                        "patch": {
                            "expected_observation_or_change": {
                                "description": "timestamp mismatch decreases without safety drift"
                            }
                        },
                        "evidence_refs": evidence_refs,
                        "reason": "make the expected observation explicit",
                    },
                )
            )
        )
        r3 = cast(dict[str, JsonValue], revised_r3_result["action"])

        portfolio_result = value(
            await runtime.bus.dispatch(
                request(
                    "action/portfolio/compose",
                    "action-portfolio",
                    {
                        "project_id": project_id,
                        "object_id": object_id,
                        "portfolio_id": "action-portfolio:full",
                        "action_ids": [
                            generated[0]["action_id"],
                            generated[2]["action_id"],
                            r3["action_id"],
                            r4["action_id"],
                        ],
                        "decision_need": "Choose a safe decision-changing next action",
                        "criteria_proposal": [
                            {
                                "criterion_id": "mandatory:policy",
                                "name": "policy admissibility",
                                "mandatory": True,
                            },
                            {
                                "criterion_id": "enhancing:information",
                                "name": "information value",
                                "mandatory": False,
                            },
                        ],
                    },
                )
            )
        )
        portfolio = cast(dict[str, JsonValue], portfolio_result["portfolio"])
        portfolio_id = str(portfolio["portfolio_id"])
        evaluated_result = value(
            await runtime.bus.dispatch(
                request(
                    "action/portfolio/evaluate",
                    "action-evaluate",
                    {
                        "project_id": project_id,
                        "portfolio_id": portfolio_id,
                        "revision_digest": portfolio["revision_digest"],
                        "evaluation_method": "SCENARIO_WITH_MANDATORY_GATES",
                        "policy_criteria_ref": "policy:current",
                        "preference_inputs": [],
                    },
                )
            )
        )
        portfolio = cast(dict[str, JsonValue], evaluated_result["portfolio"])
        assert portfolio["decision_state"] == "PREFERENCE_REQUIRED"
        recommended_result = value(
            await runtime.bus.dispatch(
                request(
                    "action/recommend",
                    "action-recommend",
                    {
                        "project_id": project_id,
                        "portfolio_id": portfolio_id,
                        "revision_digest": portfolio["revision_digest"],
                        "rationale": "prefer the lowest-risk feasible information action",
                    },
                )
            )
        )
        portfolio = cast(dict[str, JsonValue], recommended_result["portfolio"])
        selected_id = str(recommended_result["recommendation"])
        assert recommended_result["selection_implied"] is False
        selected_result = value(
            await runtime.bus.dispatch(
                request(
                    "action/select",
                    "action-select",
                    {
                        "project_id": project_id,
                        "portfolio_id": portfolio_id,
                        "action_id": selected_id,
                        "decision_context": {"reason": "bounded and reversible"},
                        "actor_or_agent_ref": "agent:decision-analysis",
                        "expected_portfolio_revision": portfolio["revision_digest"],
                    },
                )
            )
        )
        assert selected_result["authorization_implied"] is False
        selected_action = cast(dict[str, JsonValue], selected_result["action"])

        step_read = {
            "step_id": "step:read",
            "action_ref": selected_id,
            "inputs": evidence_refs,
            "output_contract": {"type": "analysis"},
            "preconditions": [],
            "timeout_seconds": 60,
            "stop_conditions": ["result available"],
            "effect_vector": {
                "effect_completeness_confirmed": True,
                "external_write": False,
            },
            "state": "READY",
        }
        step_r3 = {
            "step_id": "step:r3",
            "action_ref": r3["action_id"],
            "inputs": ["step:read"],
            "output_contract": {"type": "configuration receipt"},
            "preconditions": ["step:read complete"],
            "timeout_seconds": 600,
            "stop_conditions": ["mismatch increases"],
            "effect_vector": {
                "effect_completeness_confirmed": True,
                "external_write": True,
                "physical_action": False,
            },
            "state": "READY",
        }
        step_r4 = {
            "step_id": "step:r4",
            "action_ref": r4["action_id"],
            "inputs": [],
            "output_contract": {"type": "none"},
            "preconditions": [],
            "timeout_seconds": 1,
            "stop_conditions": ["prohibited"],
            "effect_vector": {
                "effect_completeness_confirmed": True,
                "changes_official_kpi": True,
            },
            "state": "READY",
        }
        plan_result = value(
            await runtime.bus.dispatch(
                request(
                    "action/plan/compose",
                    "action-plan",
                    {
                        "project_id": project_id,
                        "object_id": object_id,
                        "plan_id": "plan:action-full",
                        "selected_action_refs": [selected_id, r3["action_id"]],
                        "step_candidates": [step_read, step_r3, step_r4],
                        "dependency_edges": [{"from": "step:read", "to": "step:r3"}],
                    },
                )
            )
        )
        plan = cast(dict[str, JsonValue], plan_result["plan"])
        plan_id = str(plan["plan_id"])
        assert cast(list[str], plan["auto_executable_frontier"]) == ["step:read"]

        added_step_result = value(
            await runtime.bus.dispatch(
                request(
                    "action/step/add",
                    "action-step-add",
                    {
                        "project_id": project_id,
                        "plan_id": plan_id,
                        "step_spec": {
                            "step_id": "step:sandbox",
                            "action_ref": generated[2]["action_id"],
                            "inputs": ["step:read"],
                            "output_contract": {"type": "sandbox result"},
                            "preconditions": ["step:read complete"],
                            "timeout_seconds": 120,
                            "stop_conditions": ["sandbox timeout"],
                            "effect_vector": {
                                "effect_completeness_confirmed": True,
                                "runs_untrusted_code": True,
                                "sandbox_required": True,
                            },
                            "state": "READY",
                        },
                        "dependency_refs": ["step:read"],
                        "expected_plan_revision": plan["revision_digest"],
                    },
                )
            )
        )
        plan = cast(dict[str, JsonValue], added_step_result["plan"])
        revised_step_result = value(
            await runtime.bus.dispatch(
                request(
                    "action/step/revise",
                    "action-step-revise",
                    {
                        "project_id": project_id,
                        "plan_id": plan_id,
                        "step_id": "step:sandbox",
                        "patch": {"timeout_seconds": 180},
                        "evidence_refs": evidence_refs,
                        "reason": "adjust bounded sandbox timeout",
                        "expected_plan_revision": plan["revision_digest"],
                    },
                )
            )
        )
        plan = cast(dict[str, JsonValue], revised_step_result["plan"])
        revalidated_result = value(
            await runtime.bus.dispatch(
                request(
                    "action/plan/revalidate",
                    "action-plan-revalidate",
                    {
                        "project_id": project_id,
                        "plan_id": plan_id,
                        "revision_digest": plan["revision_digest"],
                        "trigger_reason": "step changed",
                    },
                )
            )
        )
        plan = cast(dict[str, JsonValue], revalidated_result["plan"])
        impact_result = value(
            await runtime.bus.dispatch(
                request(
                    "action/impact/recalculate",
                    "action-impact-recalculate",
                    {
                        "project_id": project_id,
                        "plan_id": plan_id,
                        "revision_digest": plan["revision_digest"],
                        "trigger_refs": evidence_refs,
                    },
                )
            )
        )
        plan = cast(dict[str, JsonValue], impact_result["plan"])
        policy_result = value(
            await runtime.bus.dispatch(
                request(
                    "action/policy/classify",
                    "action-policy-classify",
                    {
                        "project_id": project_id,
                        "plan_id": plan_id,
                        "revision_digest": plan["revision_digest"],
                        "policy_version": "policy:current",
                    },
                )
            )
        )
        plan = cast(dict[str, JsonValue], policy_result["plan"])
        risk_tiers = cast(dict[str, JsonValue], policy_result["risk_tiers"])
        assert risk_tiers == {
            "step:r3": "R3",
            "step:r4": "R4",
            "step:read": "R0",
            "step:sandbox": "R2",
        }

        prepared_result = value(
            await runtime.bus.dispatch(
                request(
                    "action/authorization/prepare",
                    "action-auth-prepare",
                    {
                        "project_id": project_id,
                        "plan_id": plan_id,
                        "step_id": "step:r3",
                        "plan_revision_digest": plan["revision_digest"],
                        "predecessor_output_digests": ["a" * 64],
                        "target_baseline_digests": ["b" * 64],
                        "policy_version": "policy:current",
                    },
                )
            )
        )
        authorization = cast(dict[str, JsonValue], prepared_result["authorization"])
        authorization_id = str(authorization["authorization_id"])
        assert authorization["state"] == "PENDING"
        attacker = AuthenticatedActorContext(
            actor_id="human:action-attacker",
            session_id="session:action-attacker",
            project_id=project_id,
            role_assignment_id="role:action-attacker",
            role="revision-writer",
            capabilities=("WRITE",),
            data_scopes=("PROJECT",),
        )
        with authenticated_actor_scope(attacker):
            spoofed = await runtime.bus.dispatch(
                request(
                    "action/authorization/decide",
                    "action-auth-spoofed",
                    {
                        "project_id": project_id,
                        "authorization_id": authorization_id,
                        "decision": "APPROVE",
                        "actor_ref": "human:project-owner",
                        "role_assignment_ref": role_id,
                        "approved_digest": authorization["exact_scope_digest"],
                    },
                )
            )
        assert spoofed.error is not None
        assert spoofed.error.code == -32040
        decided_result = value(
            await runtime.bus.dispatch(
                request(
                    "action/authorization/decide",
                    "action-auth-decide",
                    {
                        "project_id": project_id,
                        "authorization_id": authorization_id,
                        "decision": "APPROVE",
                        "actor_ref": "human:project-owner",
                        "role_assignment_ref": role_id,
                        "approved_digest": authorization["exact_scope_digest"],
                        "reason": "controlled change approved for exact scope",
                    },
                )
            )
        )
        decided = cast(
            dict[str, JsonValue],
            await approve_required_roles(
                runtime,
                project_id,
                cast(dict[str, JsonValue], decided_result["authorization"]),
                "human:project-owner",
                "action-additional-roles",
            ),
        )
        assert decided["state"] == "APPROVED"
        assert isinstance(
            value(
                await runtime.bus.dispatch(
                    request(
                        "action/authorization/read",
                        "action-auth-read",
                        {
                            "project_id": project_id,
                            "authorization_id": authorization_id,
                        },
                    )
                )
            )["authorization"],
            dict,
        )

        r4_prepare = await runtime.bus.dispatch(
            request(
                "action/authorization/prepare",
                "action-auth-r4",
                {
                    "project_id": project_id,
                    "plan_id": plan_id,
                    "step_id": "step:r4",
                    "plan_revision_digest": plan["revision_digest"],
                    "predecessor_output_digests": [],
                    "target_baseline_digests": ["b" * 64],
                    "policy_version": "policy:current",
                },
            )
        )
        assert r4_prepare.error is not None

        compensation_result = value(
            await runtime.bus.dispatch(
                request(
                    "action/compensation/create",
                    "action-compensation",
                    {
                        "project_id": project_id,
                        "caused_by_execution_attempt_ref": "execution:attempt-1",
                        "observed_effect_refs": ["effect:unexpected"],
                        "intended_mitigation": "restore configuration under review",
                        "residual_effect_expectation": "residual mismatch remains measurable",
                        "evidence_refs": evidence_refs,
                    },
                )
            )
        )
        compensation = cast(dict[str, JsonValue], compensation_result["action"])
        assert compensation_result["exact_rollback_claimed"] is False
        assert compensation["policy_state"] == "POLICY_UNDEFINED"

        merge_result = value(
            await runtime.bus.dispatch(
                request(
                    "action/merge/propose",
                    "action-merge",
                    {
                        "project_id": project_id,
                        "action_ids": [selected_action["action_id"], r3["action_id"]],
                        "field_mapping": {"specification": "retain-both"},
                        "evidence_refs": evidence_refs,
                        "rationale": "preview only",
                        "expected_revision_digests": [
                            selected_action["revision_digest"],
                            r3["revision_digest"],
                        ],
                    },
                )
            )
        )
        assert cast(dict[str, JsonValue], merge_result["merge_proposal"])["applied"] is False

        for method, key, params, field in (
            (
                "action/list",
                "action-list",
                {"project_id": project_id, "object_id": object_id},
                "actions",
            ),
            (
                "action/portfolio/list",
                "action-portfolio-list",
                {"project_id": project_id, "object_id": object_id},
                "portfolios",
            ),
        ):
            result = value(
                await runtime.bus.dispatch(request(method, key, cast(dict[str, object], params)))
            )
            assert cast(list[object], result[field])
        for method, key, params, field in (
            (
                "action/read",
                "action-read",
                {"project_id": project_id, "action_id": r3["action_id"]},
                "action",
            ),
            (
                "action/portfolio/read",
                "action-portfolio-read",
                {"project_id": project_id, "portfolio_id": portfolio_id},
                "portfolio",
            ),
            (
                "action/plan/read",
                "action-plan-read",
                {"project_id": project_id, "plan_id": plan_id},
                "plan",
            ),
            (
                "action/step/read",
                "action-step-read",
                {"project_id": project_id, "plan_id": plan_id, "step_id": "step:r3"},
                "step",
            ),
        ):
            result = value(
                await runtime.bus.dispatch(request(method, key, cast(dict[str, object], params)))
            )
            assert isinstance(result[field], dict)
        graph = value(
            await runtime.bus.dispatch(
                request(
                    "action/plan/graph",
                    "action-plan-graph",
                    {"project_id": project_id, "plan_id": plan_id},
                )
            )
        )
        assert len(cast(list[object], graph["nodes"])) == 4
        assert isinstance(
            value(
                await runtime.bus.dispatch(
                    request(
                        "action/impact/read",
                        "action-impact-read",
                        {"project_id": project_id, "plan_id": plan_id},
                    )
                )
            )["cumulative_impact"],
            dict,
        )
        assert isinstance(
            value(
                await runtime.bus.dispatch(
                    request(
                        "action/policy/read",
                        "action-policy-read",
                        {"project_id": project_id, "action_id": r3["action_id"]},
                    )
                )
            )["direct_impact"],
            dict,
        )
        audit = value(
            await runtime.bus.dispatch(
                request(
                    "action/audit/read",
                    "action-audit",
                    {"project_id": project_id},
                )
            )
        )
        assert len(cast(list[object], audit["records"])) >= 20
    finally:
        runtime.close()
