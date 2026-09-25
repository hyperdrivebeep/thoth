from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue
from tests.integration.research_measurement_helpers import connect_local_measurement_contract
from tests.integration.scoped_runtime import create_runtime

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
async def test_hypothesis_prediction_validity_appraisal_and_portfolio(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    inbox = workspace / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "evidence.md").write_text(
        "# Result\n\nThe interface timestamp is inconsistent.\n\n"
        "The measurement clock source changed between runs.\n",
        encoding="utf-8",
    )
    runtime = create_runtime(workspace)
    project_id = "project:hypothesis-full"
    thread_id = "thread:hypothesis-full"
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "hyp-project",
                    {
                        "project_id": project_id,
                        "name": "Hypothesis full",
                        "cutoff_at": "2026-08-31T00:00:00Z",
                    },
                )
            )
        )
        connected = value(
            await runtime.bus.dispatch(
                request(
                    "project/source/connect",
                    "hyp-source",
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
        artifact = cast(dict[str, JsonValue], connected["artifact"])
        artifact_id = str(artifact["artifact_id"])
        source_version_id = str(connected["source_version_id"])
        listed = value(
            await runtime.bus.query(
                request("project/source/list", "hyp-source-time-basis", {"project_id": project_id})
            )
        )
        source_times = cast(list[dict[str, JsonValue]], listed["source_times"])
        assessment = next(
            item for item in source_times if item["artifact_id"] == artifact_id
        )
        cutoff = cast(dict[str, JsonValue], listed["cutoff_basis"])
        assert assessment["source_version_id"] == source_version_id
        assert assessment["cutoff_state"] == "UNKNOWN_TIME"
        assert assessment["reason_code"] == "NO_DOCUMENT_DATE"
        confirmed = value(
            await runtime.bus.dispatch(
                request(
                    "project/source/time/confirm",
                    "hyp-source-time-confirm",
                    {
                        "project_id": project_id,
                        "artifact_id": artifact_id,
                        "source_version_id": source_version_id,
                        "byte_sha256": assessment["byte_sha256"],
                        "expected_project_revision": cutoff["project_revision"],
                        "expected_cutoff_at": cutoff["cutoff_at"],
                        "expected_assessment_revision": assessment["revision"],
                        "expected_metadata_digest": assessment["metadata_digest"],
                        "assertion": "ON_OR_BEFORE_CUTOFF",
                    },
                )
            )
        )
        confirmed_time = cast(dict[str, JsonValue], confirmed["source_time"])
        assert confirmed_time["cutoff_state"] == "ELIGIBLE"
        evidence = value(
            await runtime.bus.dispatch(
                request("evidence/list", "hyp-evidence", {"project_id": project_id})
            )
        )
        evidence_refs = [
            str(item["span_id"]) for item in cast(list[dict[str, JsonValue]], evidence["spans"])
        ]
        assert all(
            item["cutoff_state"] == "ELIGIBLE"
            for item in cast(list[dict[str, JsonValue]], evidence["spans"])
        )
        started = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "hyp-thread",
                    {
                        "project_id": project_id,
                        "thread_id": thread_id,
                        "problem": "Why is the interface timestamp inconsistent?",
                        "scope": {"workstream": "integration"},
                    },
                )
            )
        )
        object_id = str(cast(list[str], started["current_object_ids"])[0])

        generated_result = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/generate",
                    "hyp-generate",
                    {
                        "project_id": project_id,
                        "object_id": object_id,
                        "portfolio_id": "portfolio:hypothesis-full",
                        "question": "Why is the interface timestamp inconsistent?",
                        "evidence_scope": evidence_refs,
                        "intent_hints": ["DIAGNOSTIC_CAUSAL"],
                        "generation_policy_ref": "generation:bounded-v1",
                    },
                )
            )
        )
        generated = cast(list[dict[str, JsonValue]], generated_result["hypotheses"])
        assert len(generated) == 3
        first, second, unknown = generated
        assert unknown["primary_intent"] == "EXPLORATORY"

        explicit_result = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/create",
                    "hyp-create",
                    {
                        "project_id": project_id,
                        "object_id": object_id,
                        "portfolio_id": "portfolio:hypothesis-full",
                        "statement": "A configuration mismatch may contribute to the gap",
                        "primary_intent": "DIAGNOSTIC_CAUSAL",
                        "secondary_intents": ["EXPLORATORY"],
                        "evidence_basis": "expert-proposed and source-linked",
                        "scope": {"workstream": "integration"},
                        "evidence_refs": evidence_refs,
                        "prespecification_state": "POST_HOC",
                    },
                )
            )
        )
        explicit = cast(dict[str, JsonValue], explicit_result["hypothesis"])
        assert explicit["development_stage"] == "GROUNDED_CANDIDATE"

        causal_result = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/causal/update",
                    "hyp-causal",
                    {
                        "project_id": project_id,
                        "hypothesis_id": first["hypothesis_id"],
                        "expected_revision_digest": first["revision_digest"],
                        "causal_patch": {
                            "primary_locus": "INTERFACE_OR_INTEGRATION",
                            "contributing_loci": ["MEASUREMENT_OR_DATA"],
                            "causal_depth": "PROXIMATE",
                            "mechanism": "clock-source mismatch",
                            "validity_blockers": [],
                        },
                        "evidence_refs": evidence_refs,
                        "reason": "bind source-grounded causal facets",
                    },
                )
            )
        )
        first = cast(dict[str, JsonValue], causal_result["hypothesis"])

        intent_result = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/intent/update",
                    "hyp-intent",
                    {
                        "project_id": project_id,
                        "hypothesis_id": second["hypothesis_id"],
                        "expected_revision_digest": second["revision_digest"],
                        "primary_intent": "PREDICTIVE",
                        "secondary_intents": [],
                        "intent_profile_refs": ["intent-profile:predictive:1"],
                        "evidence_refs": evidence_refs,
                        "reason": "this candidate is predictive rather than causal",
                    },
                )
            )
        )
        second = cast(dict[str, JsonValue], intent_result["hypothesis"])
        causal_not_applicable = cast(dict[str, JsonValue], second["causal_profile"])
        assert causal_not_applicable["applicability"] == "NOT_APPLICABLE"

        counter_result = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/counterevidence/request",
                    "hyp-counter",
                    {
                        "project_id": project_id,
                        "hypothesis_id": first["hypothesis_id"],
                        "revision_digest": first["revision_digest"],
                        "source_scope": ["project sources", "counter-search"],
                    },
                )
            )
        )
        first = cast(dict[str, JsonValue], counter_result["hypothesis"])
        assert first["development_stage"] == "COUNTEREVIDENCE_CHECKED"

        assumption_result = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/assumption/add",
                    "hyp-assumption",
                    {
                        "project_id": project_id,
                        "hypothesis_id": first["hypothesis_id"],
                        "expected_revision_digest": first["revision_digest"],
                        "statement": "Both runs used comparable load conditions",
                        "role": "COMPARABILITY",
                        "evidence_refs": evidence_refs,
                        "validation_route": "compare run manifests",
                    },
                )
            )
        )
        assumption = cast(dict[str, JsonValue], assumption_result["assumption"])
        first = cast(dict[str, JsonValue], assumption_result["hypothesis"])
        assumption_id = str(assumption["assumption_id"])
        measurement_contract = await connect_local_measurement_contract(
            runtime, project_id, workspace
        )

        prediction_result = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/prediction/bind",
                    "hyp-prediction",
                    {
                        "project_id": project_id,
                        "hypothesis_id": first["hypothesis_id"],
                        "hypothesis_revision_digest": first["revision_digest"],
                        "knowledge_cutoff": "2026-08-31T00:00:00Z",
                        "prespecification_state": "A_PRIORI",
                        "conditions": {"dataset_version": "trial-v1"},
                        "measurement_contract_ref": measurement_contract,
                        "assumption_refs": [assumption_id],
                        "expected_outcome": {
                            "type": "RANGE",
                            "measure": "latency",
                            "unit": "ms",
                            "lower": 9,
                            "upper": 13,
                        },
                        "discrimination_map": {
                            "alternative": second["hypothesis_id"],
                            "separating_observation": "error unchanged",
                        },
                    },
                )
            )
        )
        prediction = cast(dict[str, JsonValue], prediction_result["prediction"])
        prediction_id = str(prediction["prediction_id"])
        first = cast(dict[str, JsonValue], prediction_result["hypothesis"])
        assert first["development_stage"] == "PREDICTION_BOUND"

        # Client labels cannot substitute for an actual execution/validity producer.
        for label, fit in (("INVALID", "MISMATCH"), ("VALID", "MATCH")):
            response = await runtime.bus.dispatch(
                request(
                    "hypothesis/test/bind",
                    "unbound-test-" + label,
                    {
                        "project_id": project_id,
                        "prediction_id": prediction_id,
                        "execution_ref": "execution:unmaterialized-" + label,
                        "observation_refs": evidence_refs,
                        "test_validity_assessment_ref": "validity:unmaterialized-" + label,
                        "test_validity": label,
                        "prediction_fit": fit,
                    },
                )
            )
            assert response.error is not None
            assert "TEST_VALIDITY_PRODUCER_NOT_FOUND" in response.error.message
            assessed = value(
                await runtime.bus.dispatch(
                    request(
                        "hypothesis/appraise",
                        "unconfirmed-appraisal-" + label,
                        {
                            "project_id": project_id,
                            "hypothesis_id": first["hypothesis_id"],
                            "revision_digest": first["revision_digest"],
                            "evidence_refs": evidence_refs,
                            "test_assessment_refs": [],
                            "appraisal_scope": {"dataset_version": "trial-v1"},
                        },
                    )
                )
            )
            assert (
                cast(dict[str, JsonValue], assessed["appraisal"])["substantive_update_applied"]
                is False
            )
            first = cast(dict[str, JsonValue], assessed["hypothesis"])
            assert first["empirical_appraisal"] == "UNASSESSED"

        revised_second_result = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/revise",
                    "hyp-revise",
                    {
                        "project_id": project_id,
                        "hypothesis_id": second["hypothesis_id"],
                        "expected_revision_digest": second["revision_digest"],
                        "patch": {
                            "statement": ("A workload distribution shift predicts the observed gap")
                        },
                        "evidence_refs": evidence_refs,
                        "reason": "clarify the predictive candidate",
                    },
                )
            )
        )
        second = cast(dict[str, JsonValue], revised_second_result["hypothesis"])

        portfolio_result = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/portfolio/compose",
                    "hyp-portfolio",
                    {
                        "project_id": project_id,
                        "object_id": object_id,
                        "portfolio_id": "portfolio:hypothesis-full",
                        "hypothesis_ids": [
                            first["hypothesis_id"],
                            second["hypothesis_id"],
                            unknown["hypothesis_id"],
                        ],
                        "unknown_reserve": {
                            "hypothesis_id": unknown["hypothesis_id"],
                            "abstention_allowed": True,
                        },
                    },
                )
            )
        )
        portfolio = cast(dict[str, JsonValue], portfolio_result["portfolio"])
        portfolio_id = str(portfolio["portfolio_id"])

        relation_result = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/relation/add",
                    "hyp-relation-add",
                    {
                        "project_id": project_id,
                        "portfolio_id": portfolio_id,
                        "source_hypothesis_id": first["hypothesis_id"],
                        "relation_type": "ALTERNATIVE_TO",
                        "target_hypothesis_id": second["hypothesis_id"],
                        "evidence_refs": evidence_refs,
                        "semantic_role": "competing explanations",
                        "expected_portfolio_revision": portfolio["revision_digest"],
                    },
                )
            )
        )
        relation = cast(dict[str, JsonValue], relation_result["relation"])
        portfolio = cast(dict[str, JsonValue], relation_result["portfolio"])
        assert relation["active"] is True
        removed_relation_result = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/relation/remove",
                    "hyp-relation-remove",
                    {
                        "project_id": project_id,
                        "relation_id": relation["relation_id"],
                        "reason": "replace relation after portfolio revision",
                        "evidence_refs": evidence_refs,
                        "expected_portfolio_revision": portfolio["revision_digest"],
                    },
                )
            )
        )
        ended_relation = cast(dict[str, JsonValue], removed_relation_result["relation"])
        portfolio = cast(dict[str, JsonValue], removed_relation_result["portfolio"])
        assert ended_relation["active"] is False

        revalidated_portfolio_result = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/portfolio/revalidate",
                    "hyp-portfolio-revalidate",
                    {
                        "project_id": project_id,
                        "portfolio_id": portfolio_id,
                        "revision_digest": portfolio["revision_digest"],
                        "trigger_reason": "prediction and appraisal changed",
                    },
                )
            )
        )
        portfolio = cast(dict[str, JsonValue], revalidated_portfolio_result["portfolio"])

        split_result = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/split/propose",
                    "hyp-split",
                    {
                        "project_id": project_id,
                        "hypothesis_id": first["hypothesis_id"],
                        "expected_revision_digest": first["revision_digest"],
                        "subhypotheses": [
                            {"statement": "clock source differs"},
                            {"statement": "timestamp conversion differs"},
                        ],
                        "evidence_refs": evidence_refs,
                        "rationale": "preview separable mechanisms",
                    },
                )
            )
        )
        assert cast(dict[str, JsonValue], split_result["split_proposal"])["applied"] is False

        merge_result = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/merge/propose",
                    "hyp-merge",
                    {
                        "project_id": project_id,
                        "hypothesis_ids": [first["hypothesis_id"], second["hypothesis_id"]],
                        "field_mapping": {"statement": "retain-both"},
                        "evidence_refs": evidence_refs,
                        "rationale": "preview only; never auto-merge similarity",
                        "expected_revision_digests": [
                            first["revision_digest"],
                            second["revision_digest"],
                        ],
                    },
                )
            )
        )
        assert cast(dict[str, JsonValue], merge_result["merge_proposal"])["applied"] is False

        for method, key, params, field in (
            (
                "hypothesis/list",
                "hyp-list",
                {"project_id": project_id, "object_id": object_id},
                "hypotheses",
            ),
            (
                "hypothesis/graph/read",
                "hyp-graph",
                {"project_id": project_id, "object_id": object_id},
                "nodes",
            ),
            (
                "hypothesis/portfolio/list",
                "hyp-portfolio-list",
                {"project_id": project_id, "object_id": object_id},
                "portfolios",
            ),
            (
                "hypothesis/relation/list",
                "hyp-relation-list",
                {"project_id": project_id, "portfolio_id": portfolio_id},
                "relations",
            ),
            (
                "hypothesis/prediction/list",
                "hyp-prediction-list",
                {"project_id": project_id, "hypothesis_id": first["hypothesis_id"]},
                "predictions",
            ),
            (
                "hypothesis/assumption/list",
                "hyp-assumption-list",
                {"project_id": project_id, "hypothesis_id": first["hypothesis_id"]},
                "assumptions",
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
                        "hypothesis/read",
                        "hyp-read",
                        {
                            "project_id": project_id,
                            "hypothesis_id": first["hypothesis_id"],
                        },
                    )
                )
            )["hypothesis"],
            dict,
        )
        assert isinstance(
            value(
                await runtime.bus.dispatch(
                    request(
                        "hypothesis/portfolio/read",
                        "hyp-portfolio-read",
                        {"project_id": project_id, "portfolio_id": portfolio_id},
                    )
                )
            )["portfolio"],
            dict,
        )
        assert isinstance(
            value(
                await runtime.bus.dispatch(
                    request(
                        "hypothesis/prediction/read",
                        "hyp-prediction-read",
                        {"project_id": project_id, "prediction_id": prediction_id},
                    )
                )
            )["prediction"],
            dict,
        )
        quality = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/quality/read",
                    "hyp-quality",
                    {
                        "project_id": project_id,
                        "hypothesis_id": first["hypothesis_id"],
                    },
                )
            )
        )
        assert quality["rationale"] == "axes remain non-compensatory and are never averaged"
        appraisal = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/appraisal/read",
                    "hyp-appraisal-read",
                    {
                        "project_id": project_id,
                        "hypothesis_id": first["hypothesis_id"],
                    },
                )
            )
        )
        assert appraisal["current_scoped_appraisal"] == "UNASSESSED"
        audit = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/audit/read",
                    "hyp-audit",
                    {
                        "project_id": project_id,
                        "hypothesis_id": first["hypothesis_id"],
                    },
                )
            )
        )
        assert len(cast(list[object], audit["records"])) >= 8
    finally:
        runtime.close()
