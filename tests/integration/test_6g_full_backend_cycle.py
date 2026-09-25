from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue
from tests.integration.test_four_projectpack_portability import GenericProjectPackModel

from thoth.adapters.projectpacks import load_project_pack
from thoth.apps.projectpack_execution import run_project_pack
from thoth.apps.runtime import create_runtime
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
async def test_6g_reference_pack_reaches_full_backend_learning_cycle(tmp_path: Path) -> None:
    pack_root = (
        Path(__file__).resolve().parents[2] / "examples" / "projectpacks" / "6g-sandbox-hero"
    )
    pack = load_project_pack(pack_root)
    workspace = tmp_path / "6g-full"
    initial = await run_project_pack(
        pack,
        workspace=workspace,
        model=GenericProjectPackModel(),
    )
    assert len(initial.cycle.portfolio.hypotheses) == 3
    runtime = create_runtime(workspace)
    project_id = pack.project.project_id
    object_id = pack.scenario.object_id
    try:
        evidence_result = value(
            await runtime.bus.dispatch(
                request("evidence/list", "6g-evidence", {"project_id": project_id})
            )
        )
        spans = cast(list[dict[str, JsonValue]], evidence_result["spans"])
        eligible_refs = [
            str(item["span_id"]) for item in spans if item["cutoff_state"] == "ELIGIBLE"
        ]
        after_cutoff_refs = [
            str(item["span_id"]) for item in spans if item["cutoff_state"] == "AFTER_CUTOFF"
        ]
        assert eligible_refs and after_cutoff_refs

        criteria_result = value(
            await runtime.bus.dispatch(
                request(
                    "criteria/compile",
                    "6g-criteria",
                    {
                        "project_id": project_id,
                        "thread_id": pack.scenario.thread_id,
                        "source_span_ids": eligible_refs[:4],
                        "goal_requirement_refs": ["reference:6g-evaluation"],
                        "profile_refs": ["SYSTEMS_ENGINEERING_VERIFICATION"],
                    },
                )
            )
        )
        criterion = cast(dict[str, JsonValue], criteria_result["criterion"])
        assert criterion["usage_authorization"] == "NOT_AUTHORIZED"

        hypotheses_result = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/generate",
                    "6g-hypotheses",
                    {
                        "project_id": project_id,
                        "object_id": object_id,
                        "portfolio_id": "portfolio:6g-full",
                        "question": pack.scenario.problem,
                        "evidence_scope": eligible_refs[:4],
                        "intent_hints": ["DIAGNOSTIC_CAUSAL"],
                        "generation_policy_ref": "generation:bounded-v1",
                    },
                )
            )
        )
        full_hypotheses = cast(list[dict[str, JsonValue]], hypotheses_result["hypotheses"])
        assert len(full_hypotheses) >= 3
        assert all(ref not in str(hypotheses_result) for ref in after_cutoff_refs)

        actions_result = value(
            await runtime.bus.dispatch(
                request(
                    "action/generate",
                    "6g-actions",
                    {
                        "project_id": project_id,
                        "object_id": object_id,
                        "hypothesis_refs": [item["hypothesis_id"] for item in full_hypotheses[:2]],
                        "decision_need": "resolve cross-site comparison gaps",
                        "evidence_scope": eligible_refs[:4],
                        "purpose_hints": ["HYPOTHESIS_DISCRIMINATION"],
                    },
                )
            )
        )
        full_actions = cast(list[dict[str, JsonValue]], actions_result["actions"])
        portfolio_result = value(
            await runtime.bus.dispatch(
                request(
                    "action/portfolio/compose",
                    "6g-action-portfolio",
                    {
                        "project_id": project_id,
                        "object_id": object_id,
                        "portfolio_id": "action-portfolio:6g-full",
                        "action_ids": [item["action_id"] for item in full_actions],
                        "decision_need": "select a bounded information-gaining action",
                        "criteria_proposal": [],
                    },
                )
            )
        )
        portfolio = cast(dict[str, JsonValue], portfolio_result["portfolio"])
        evaluated_result = value(
            await runtime.bus.dispatch(
                request(
                    "action/portfolio/evaluate",
                    "6g-action-evaluate",
                    {
                        "project_id": project_id,
                        "portfolio_id": portfolio["portfolio_id"],
                        "revision_digest": portfolio["revision_digest"],
                        "evaluation_method": "SCENARIO_WITH_MANDATORY_GATES",
                        "policy_criteria_ref": "policy:6g-sandbox-reference-v1",
                        "preference_inputs": [{"prefer": "lower risk before protected rerun"}],
                    },
                )
            )
        )
        portfolio = cast(dict[str, JsonValue], evaluated_result["portfolio"])
        recommended_result = value(
            await runtime.bus.dispatch(
                request(
                    "action/recommend",
                    "6g-action-recommend",
                    {
                        "project_id": project_id,
                        "portfolio_id": portfolio["portfolio_id"],
                        "revision_digest": portfolio["revision_digest"],
                        "rationale": "bounded evidence acquisition precedes rerun",
                    },
                )
            )
        )
        portfolio = cast(dict[str, JsonValue], recommended_result["portfolio"])
        selected_result = value(
            await runtime.bus.dispatch(
                request(
                    "action/select",
                    "6g-action-select",
                    {
                        "project_id": project_id,
                        "portfolio_id": portfolio["portfolio_id"],
                        "action_id": recommended_result["recommendation"],
                        "decision_context": {
                            "reason": "highest decision value under current authority"
                        },
                        "actor_or_agent_ref": "agent:6g-decision-analysis",
                        "expected_portfolio_revision": portfolio["revision_digest"],
                    },
                )
            )
        )
        selected = cast(dict[str, JsonValue], selected_result["action"])
        plan_result = value(
            await runtime.bus.dispatch(
                request(
                    "action/plan/compose",
                    "6g-plan",
                    {
                        "project_id": project_id,
                        "object_id": object_id,
                        "plan_id": "plan:6g-full",
                        "selected_action_refs": [selected["action_id"]],
                        "step_candidates": [
                            {
                                "step_id": "step:6g-read",
                                "action_ref": selected["action_id"],
                                "inputs": eligible_refs[:2],
                                "output_contract": {"type": "comparison-manifest"},
                                "preconditions": [],
                                "stop_conditions": ["manifest comparison complete"],
                                "effect_vector": {
                                    "effect_completeness_confirmed": True,
                                    "external_write": False,
                                },
                                "state": "READY",
                            }
                        ],
                        "dependency_edges": [],
                    },
                )
            )
        )
        plan = cast(dict[str, JsonValue], plan_result["plan"])

        new_evidence_file = workspace / "inbox" / "new-evidence.md"
        new_evidence_file.parent.mkdir(parents=True, exist_ok=True)
        new_evidence_file.write_text(
            "# New evidence\n\nA source-bound configuration manifest is now available.\n",
            encoding="utf-8",
        )
        connected_result = value(
            await runtime.bus.dispatch(
                request(
                    "project/source/connect",
                    "6g-new-evidence",
                    {
                        "project_id": project_id,
                        "relative_path": "new-evidence.md",
                        "media_type": "text/markdown",
                        "authority": "OFFICIAL",
                        "cutoff_state": "ELIGIBLE",
                        "security_class": "INTERNAL",
                    },
                )
            )
        )
        new_artifact = cast(dict[str, JsonValue], connected_result["artifact"])
        all_spans = cast(
            list[dict[str, JsonValue]],
            value(
                await runtime.bus.dispatch(
                    request(
                        "evidence/list",
                        "6g-evidence-after",
                        {"project_id": project_id},
                    )
                )
            )["spans"],
        )
        new_refs = [
            str(item["span_id"])
            for item in all_spans
            if item["artifact_id"] == new_artifact["artifact_id"]
        ]
        assert new_refs

        series_result = value(
            await runtime.bus.dispatch(
                request(
                    "outcome/series/create",
                    "6g-outcome-series",
                    {
                        "project_id": project_id,
                        "object_id": object_id,
                        "action_plan_revision_digest": plan["revision_digest"],
                        "profile_ref": "EXPERIMENT_LEARNING_OUTCOME",
                        "comparison_baseline_set_digest": head_set_digest(
                            runtime.ledger.read_heads(project_id)
                        ),
                        "assessment_windows": [
                            {
                                "assessment_phase": "INTERIM",
                                "window": "new evidence arrival",
                            }
                        ],
                    },
                )
            )
        )
        series = cast(dict[str, JsonValue], series_result["series"])
        linked_result = value(
            await runtime.bus.dispatch(
                request(
                    "outcome/observation/link",
                    "6g-outcome-link",
                    {
                        "project_id": project_id,
                        "outcome_series_id": series["outcome_series_id"],
                        "assessment_phase": "INTERIM",
                        "observation_refs": new_refs,
                        "completeness": "COMPLETE",
                        "evidence_refs": new_refs,
                        "expected_series_revision": series["revision"],
                    },
                )
            )
        )
        series = cast(dict[str, JsonValue], linked_result["series"])
        outcome_result = value(
            await runtime.bus.dispatch(
                request(
                    "outcome/assess",
                    "6g-outcome-assess",
                    {
                        "project_id": project_id,
                        "outcome_series_id": series["outcome_series_id"],
                        "assessment_phase": "INTERIM",
                        "profile_version": 1,
                        "observation_refs": new_refs,
                        "comparator_refs": eligible_refs[:1],
                        "assumptions": ["reference packet remains non-authoritative"],
                        "expected_series_revision": series["revision"],
                    },
                )
            )
        )
        outcome = cast(dict[str, JsonValue], outcome_result["assessment"])
        assert outcome["objective_attainment"] == "NOT_ASSESSED"

        object_result = value(
            await runtime.bus.dispatch(
                request(
                    "object/read",
                    "6g-object-read",
                    {"project_id": project_id, "object_id": object_id},
                )
            )
        )
        object_value = cast(dict[str, JsonValue], object_result["object"])
        old_head = str(object_value["revision_digest"])
        changed_content = {
            **object_value,
            "purpose_statement": "Review new evidence without overwriting history",
        }
        proposal_result = value(
            await runtime.bus.dispatch(
                request(
                    "revision/propose",
                    "6g-revision-propose",
                    {
                        "project_id": project_id,
                        "aggregate_id": object_id,
                        "aggregate_type": "DECISION_OBJECT",
                        "parent_revision_digests": [old_head],
                        "candidate_content": changed_content,
                        "reason": "new evidence changed the working frame",
                        "evidence_refs": new_refs,
                        "actor_or_agent_ref": "agent:6g-cycle",
                        "expected_head_digest": old_head,
                    },
                )
            )
        )
        proposal = cast(dict[str, JsonValue], proposal_result["proposal"])
        heads_before = dict(runtime.ledger.read_heads(project_id))
        change_set_result = value(
            await runtime.bus.dispatch(
                request(
                    "revision/changeSet/create",
                    "6g-changeset",
                    {
                        "project_id": project_id,
                        "expected_head_set": heads_before,
                        "candidate_revision_digests": [proposal["record_digest"]],
                        "transition_reason": "new evidence revision",
                        "impact_policy_ref": "impact:default",
                    },
                )
            )
        )
        change_set = cast(dict[str, JsonValue], change_set_result["change_set"])
        validated = value(
            await runtime.bus.dispatch(
                request(
                    "revision/changeSet/validate",
                    "6g-changeset-validate",
                    {
                        "project_id": project_id,
                        "record_id": change_set["record_id"],
                        "expected_change_set_revision": change_set["version"],
                    },
                )
            )
        )
        committed = value(
            await runtime.bus.dispatch(
                request(
                    "revision/changeSet/commit",
                    "6g-changeset-commit",
                    {
                        "project_id": project_id,
                        "record_id": change_set["record_id"],
                        "validation_bundle_digest": validated["validation_bundle_digest"],
                        "expected_head_set_digest": head_set_digest(heads_before),
                    },
                )
            )
        )
        new_head = cast(dict[str, str], committed["new_project_head_set"])[
            f"DECISION_OBJECT:{object_id}"
        ]
        restore = value(
            await runtime.bus.dispatch(
                request(
                    "revision/restore/propose",
                    "6g-restore",
                    {
                        "project_id": project_id,
                        "aggregate_id": object_id,
                        "current_head_digest": new_head,
                        "target_revision_digest": old_head,
                        "reason": "restore prior content as a new child revision",
                        "evidence_refs": new_refs,
                        "actor_or_agent_ref": "human:6g-reviewer",
                    },
                )
            )
        )
        assert restore["external_effect_rollback_claimed"] is False

        memory_candidate = cast(
            dict[str, JsonValue],
            value(
                await runtime.bus.dispatch(
                    request(
                        "memory/candidate/create",
                        "6g-memory-candidate",
                        {
                            "project_id": project_id,
                            "producer_role": "Reflection",
                            "payload_mode": "MEMORY_ASSERTION",
                            "recall_class": "LESSON",
                            "assertion_candidate": (
                                "Cross-site comparison requires method and configuration lineage."
                            ),
                            "parent_refs": [outcome["revision_digest"]],
                            "scope": {"workstream": "integration"},
                            "evidence_refs": new_refs,
                            "risk_tags": [],
                        },
                    )
                )
            )["candidate"],
        )
        validated_memory = value(
            await runtime.bus.dispatch(
                request(
                    "memory/validate",
                    "6g-memory-validate",
                    {
                        "project_id": project_id,
                        "candidate_id": memory_candidate["record_id"],
                        "transition_intent": "COMMIT_CANDIDATE",
                        "policy_version": "memory:1",
                    },
                )
            )
        )
        assert validated_memory["reducer_outcome"] == "VALIDATED"
        receipts = cast(
            list[dict[str, JsonValue]],
            value(
                await runtime.bus.dispatch(
                    request(
                        "receipt/list",
                        "6g-receipts",
                        {"project_id": project_id},
                    )
                )
            )["receipts"],
        )
        assert receipts
        verification = value(
            await runtime.bus.dispatch(
                request(
                    "receipt/verify",
                    "6g-receipt-verify",
                    {
                        "project_id": project_id,
                        "receipt_id": receipts[-1]["receipt_id"],
                    },
                )
            )
        )
        assert cast(dict[str, JsonValue], verification["verification"])["state"] == "VERIFIED"
    finally:
        runtime.close()
