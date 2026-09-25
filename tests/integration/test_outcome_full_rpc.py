from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue
from tests.integration.scoped_runtime import create_runtime

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
async def test_outcome_levels_attribution_changeset_followup_and_impact(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    inbox = workspace / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "evidence.md").write_text(
        "# Outcome evidence\n\nThe controlled replay produced a measurable result.\n",
        encoding="utf-8",
    )
    runtime = create_runtime(workspace)
    project_id = "project:outcome-full"
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "outcome-project",
                    {
                        "project_id": project_id,
                        "name": "Outcome full",
                        "cutoff_at": "2026-08-31T00:00:00Z",
                    },
                )
            )
        )
        value(
            await runtime.bus.dispatch(
                request(
                    "project/source/connect",
                    "outcome-source",
                    {
                        "project_id": project_id,
                        "relative_path": "evidence.md",
                        "media_type": "text/markdown",
                        "authority": "OFFICIAL",
                        "cutoff_state": "ELIGIBLE",
                        "security_class": "INTERNAL",
                    },
                )
            )
        )
        evidence = value(
            await runtime.bus.dispatch(
                request("evidence/list", "outcome-evidence", {"project_id": project_id})
            )
        )
        evidence_refs = [
            str(item["span_id"]) for item in cast(list[dict[str, JsonValue]], evidence["spans"])
        ]
        thread = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "outcome-thread",
                    {
                        "project_id": project_id,
                        "thread_id": "thread:outcome-full",
                        "problem": "What changed after the controlled replay?",
                        "scope": {"workstream": "integration"},
                    },
                )
            )
        )
        object_id = str(cast(list[str], thread["current_object_ids"])[0])
        action_result = value(
            await runtime.bus.dispatch(
                request(
                    "action/create",
                    "outcome-action",
                    {
                        "project_id": project_id,
                        "object_id": object_id,
                        "primary_purpose": "EXPERIMENT_TEST",
                        "specification": {
                            "description": "Run controlled replay",
                            "expected_observation_or_change": {
                                "description": "result becomes measurable"
                            },
                            "effect_completeness_confirmed": True,
                            "stop_conditions": ["result captured"],
                            "observability": "test receipt",
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
        action = cast(dict[str, JsonValue], action_result["action"])
        plan_result = value(
            await runtime.bus.dispatch(
                request(
                    "action/plan/compose",
                    "outcome-plan",
                    {
                        "project_id": project_id,
                        "object_id": object_id,
                        "plan_id": "plan:outcome-full",
                        "selected_action_refs": [action["action_id"]],
                        "step_candidates": [
                            {
                                "step_id": "step:replay",
                                "action_ref": action["action_id"],
                                "inputs": evidence_refs,
                                "output_contract": {"type": "measurement"},
                                "preconditions": [],
                                "stop_conditions": ["result captured"],
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

        profiles = value(
            await runtime.bus.dispatch(
                request("outcome/profile/list", "outcome-profiles", {"project_id": project_id})
            )
        )
        assert len(cast(list[object], profiles["profiles"])) == 3
        assert isinstance(
            value(
                await runtime.bus.dispatch(
                    request(
                        "outcome/profile/read",
                        "outcome-profile-read",
                        {
                            "project_id": project_id,
                            "profile_ref": "EXPERIMENT_LEARNING_OUTCOME",
                        },
                    )
                )
            )["profile"],
            dict,
        )

        series_result = value(
            await runtime.bus.dispatch(
                request(
                    "outcome/series/create",
                    "outcome-series",
                    {
                        "project_id": project_id,
                        "object_id": object_id,
                        "action_plan_revision_digest": plan["revision_digest"],
                        "profile_ref": "EXPERIMENT_LEARNING_OUTCOME",
                        "comparison_baseline_set_digest": "a" * 64,
                        "assessment_windows": [
                            {
                                "assessment_phase": "INTERIM",
                                "window": "immediate replay",
                            },
                            {
                                "assessment_phase": "FINAL_WITHIN_SCOPE",
                                "window": "after validation",
                            },
                        ],
                    },
                )
            )
        )
        series = cast(dict[str, JsonValue], series_result["series"])
        series_id = str(series["outcome_series_id"])
        linked_result = value(
            await runtime.bus.dispatch(
                request(
                    "outcome/observation/link",
                    "outcome-link",
                    {
                        "project_id": project_id,
                        "outcome_series_id": series_id,
                        "assessment_phase": "INTERIM",
                        "observation_refs": evidence_refs,
                        "completeness": "PARTIAL",
                        "evidence_refs": evidence_refs,
                        "expected_series_revision": series["revision"],
                    },
                )
            )
        )
        series = cast(dict[str, JsonValue], linked_result["series"])
        assert linked_result["assessment_state"] == "READY_TO_ASSESS"
        assessed_result = value(
            await runtime.bus.dispatch(
                request(
                    "outcome/assess",
                    "outcome-assess",
                    {
                        "project_id": project_id,
                        "outcome_series_id": series_id,
                        "assessment_phase": "INTERIM",
                        "profile_version": 1,
                        "observation_refs": evidence_refs,
                        "comparator_refs": evidence_refs,
                        "assumptions": ["comparator is applicable within this replay"],
                        "expected_series_revision": series["revision"],
                    },
                )
            )
        )
        assessment = cast(dict[str, JsonValue], assessed_result["assessment"])
        assessment_id = str(assessment["outcome_assessment_id"])
        assert assessment["validity"] == "LIMITED"
        assert assessment["objective_attainment"] == "NOT_ASSESSED"
        assert assessment["attribution_state"] == "NOT_ASSESSED"

        attribution_result = value(
            await runtime.bus.dispatch(
                request(
                    "outcome/attribution/assess",
                    "outcome-attribution",
                    {
                        "project_id": project_id,
                        "outcome_assessment_id": assessment_id,
                        "attribution_method": "RANDOMIZED",
                        "contextual_factor_refs": ["context:controlled-replay"],
                        "counterfactual_evidence_refs": evidence_refs,
                        "evidence_refs": evidence_refs,
                    },
                )
            )
        )
        attribution = cast(dict[str, JsonValue], attribution_result["attribution"])
        assessment = cast(dict[str, JsonValue], attribution_result["assessment"])
        assert attribution["supported_level"] == "CAUSAL_ATTRIBUTION_SUPPORTED"

        change_set_result = value(
            await runtime.bus.dispatch(
                request(
                    "outcome/changeSet/propose",
                    "outcome-changeset",
                    {
                        "project_id": project_id,
                        "outcome_assessment_id": assessment_id,
                        "proposed_entity_changes": {
                            "Hypothesis": {"freshness": "STALE"},
                            "Improvement": {"candidate": "improve comparator evidence"},
                        },
                        "impact_policy_ref": "impact:default-v1",
                        "expected_project_head_set": head_set_digest(
                            runtime.ledger.read_heads(project_id)
                        ),
                    },
                )
            )
        )
        change_set = cast(dict[str, JsonValue], change_set_result["change_set"])
        change_set_id = str(change_set["outcome_change_set_id"])
        assert change_set["status"] == "PROPOSED_NOT_APPLIED"
        assert "NO_BASELINE_MOVEMENT" in cast(list[str], change_set["validity_restrictions"])
        assert change_set_result["current_state_mutated"] is False

        followup_result = value(
            await runtime.bus.dispatch(
                request(
                    "outcome/followup/generate",
                    "outcome-followup",
                    {
                        "project_id": project_id,
                        "outcome_assessment_id": assessment_id,
                        "follow_up_scope": "resolve partial observation coverage",
                    },
                )
            )
        )
        followup = cast(dict[str, JsonValue], followup_result["candidates"])
        action_candidate = cast(dict[str, JsonValue], followup["action_candidate"])
        assert action_candidate["automatic_execution"] is False

        impact_result = value(
            await runtime.bus.dispatch(
                request(
                    "outcome/impact/propose",
                    "outcome-impact",
                    {
                        "project_id": project_id,
                        "outcome_series_id": series_id,
                        "broader_window": "six months",
                        "impact_profile_ref": "impact:program-level",
                        "evidence_refs": evidence_refs,
                        "attribution_design_ref": "TEMPORAL_ONLY",
                    },
                )
            )
        )
        impact = cast(dict[str, JsonValue], impact_result["impact"])
        impact_id = str(impact["impact_assessment_id"])
        assert impact["status"] == "INSUFFICIENT_CAUSAL_DESIGN"

        reassessed_result = value(
            await runtime.bus.dispatch(
                request(
                    "outcome/reassess",
                    "outcome-reassess",
                    {
                        "project_id": project_id,
                        "outcome_assessment_id": assessment_id,
                        "new_evidence_refs": evidence_refs,
                        "reason": "additional evidence became available",
                        "expected_revision_digest": assessment["revision_digest"],
                    },
                )
            )
        )
        reassessed = cast(dict[str, JsonValue], reassessed_result["assessment"])
        assert reassessed_result["prior_assessment_preserved"] is True
        assert reassessed["supersedes_revision_digest"] == assessment["revision_digest"]

        for method, key, params, field in (
            (
                "outcome/list",
                "outcome-list",
                {"project_id": project_id, "object_id": object_id},
                "outcomes",
            ),
            (
                "outcome/series/list",
                "outcome-series-list",
                {"project_id": project_id, "object_id": object_id},
                "series",
            ),
        ):
            result = value(
                await runtime.bus.dispatch(request(method, key, cast(dict[str, object], params)))
            )
            assert cast(list[object], result[field])
        for method, key, params, field in (
            (
                "outcome/read",
                "outcome-read",
                {
                    "project_id": project_id,
                    "outcome_assessment_id": assessment_id,
                },
                "outcome",
            ),
            (
                "outcome/series/read",
                "outcome-series-read",
                {"project_id": project_id, "outcome_series_id": series_id},
                "series",
            ),
            (
                "outcome/attribution/read",
                "outcome-attribution-read",
                {
                    "project_id": project_id,
                    "attribution_assessment_id": attribution["attribution_assessment_id"],
                },
                "attribution",
            ),
            (
                "outcome/changeSet/read",
                "outcome-changeset-read",
                {
                    "project_id": project_id,
                    "outcome_change_set_id": change_set_id,
                },
                "change_set",
            ),
            (
                "outcome/impact/read",
                "outcome-impact-read",
                {"project_id": project_id, "impact_assessment_id": impact_id},
                "impact",
            ),
        ):
            result = value(
                await runtime.bus.dispatch(request(method, key, cast(dict[str, object], params)))
            )
            assert isinstance(result[field], dict)
        audit = value(
            await runtime.bus.dispatch(
                request(
                    "outcome/audit/read",
                    "outcome-audit",
                    {"project_id": project_id},
                )
            )
        )
        assert len(cast(list[object], audit["records"])) >= 8
    finally:
        runtime.close()
