from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue
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
async def test_criteria_contract_revision_reference_conflict_and_recalculation(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    inbox = workspace / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "plan.md").write_text(
        "# Latency criterion\n\n"
        "The accepted latency is below 1 ms.\n\n"
        "The score is calculated as total / count.\n",
        encoding="utf-8",
    )
    runtime = create_runtime(workspace)
    project_id = "project:criteria-full"
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "criteria-project",
                    {
                        "project_id": project_id,
                        "name": "Criteria full",
                        "cutoff_at": "2026-08-31T00:00:00Z",
                    },
                )
            )
        )
        value(
            await runtime.bus.dispatch(
                request(
                    "project/source/connect",
                    "criteria-source",
                    {
                        "project_id": project_id,
                        "relative_path": "plan.md",
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
                request("evidence/list", "criteria-evidence", {"project_id": project_id})
            )
        )
        spans = cast(list[dict[str, JsonValue]], evidence["spans"])
        span_ids = [str(item["span_id"]) for item in spans]
        assert span_ids

        profiles = value(
            await runtime.bus.dispatch(
                request("criteria/profile/list", "criteria-profiles", {"project_id": project_id})
            )
        )
        profile_items = cast(list[dict[str, JsonValue]], profiles["profiles"])
        assert len(profile_items) == 6
        profile_read = value(
            await runtime.bus.dispatch(
                request(
                    "criteria/profile/read",
                    "criteria-profile-read",
                    {
                        "project_id": project_id,
                        "profile_ref": "SYSTEMS_ENGINEERING_VERIFICATION",
                    },
                )
            )
        )
        assert isinstance(profile_read["profile"], dict)

        compiled_result = value(
            await runtime.bus.dispatch(
                request(
                    "criteria/compile",
                    "criteria-compile",
                    {
                        "project_id": project_id,
                        "source_span_ids": span_ids,
                        "goal_requirement_refs": ["REQ-LATENCY-001"],
                        "profile_refs": ["SYSTEMS_ENGINEERING_VERIFICATION"],
                    },
                )
            )
        )
        compiled = cast(dict[str, JsonValue], compiled_result["criterion"])
        criterion_id = str(compiled["criterion_id"])
        assert compiled["usage_authorization"] == "NOT_AUTHORIZED"
        assert compiled["completeness"] == "COMPLETE"

        profile_applied_result = value(
            await runtime.bus.dispatch(
                request(
                    "criteria/profile/apply",
                    "criteria-profile-apply",
                    {
                        "project_id": project_id,
                        "criterion_id": criterion_id,
                        "expected_revision_digest": compiled["revision_digest"],
                        "profile_refs": ["SYSTEMS_ENGINEERING_VERIFICATION"],
                        "reason": "confirm the project verification profile",
                    },
                )
            )
        )
        compiled = cast(dict[str, JsonValue], profile_applied_result["criterion"])
        assert profile_applied_result["newly_required_fields"] == []

        corrected_expression_result = value(
            await runtime.bus.dispatch(
                request(
                    "criteria/field/correct",
                    "criteria-expression",
                    {
                        "project_id": project_id,
                        "criterion_id": criterion_id,
                        "expected_revision_digest": compiled["revision_digest"],
                        "field_path": "computation_spec.expression",
                        "proposed_value": "total / count",
                        "evidence_span_ids": span_ids,
                        "reason": "bind the explicit source formula",
                    },
                )
            )
        )
        corrected_expression = cast(dict[str, JsonValue], corrected_expression_result["criterion"])
        evaluator_digest = "a" * 64
        corrected_digest_result = value(
            await runtime.bus.dispatch(
                request(
                    "criteria/field/correct",
                    "criteria-binding",
                    {
                        "project_id": project_id,
                        "criterion_id": criterion_id,
                        "expected_revision_digest": corrected_expression["revision_digest"],
                        "field_path": "computation_spec.evaluator_binding_digest",
                        "proposed_value": evaluator_digest,
                        "evidence_span_ids": span_ids,
                        "reason": "bind the deterministic evaluator revision",
                    },
                )
            )
        )
        corrected_digest = cast(dict[str, JsonValue], corrected_digest_result["criterion"])

        validated_result = value(
            await runtime.bus.dispatch(
                request(
                    "criteria/revalidate",
                    "criteria-revalidate",
                    {
                        "project_id": project_id,
                        "criterion_id": criterion_id,
                        "revision_digest": corrected_digest["revision_digest"],
                        "trigger_reason": "formula and evaluator are now source-bound",
                    },
                )
            )
        )
        validated = cast(dict[str, JsonValue], validated_result["criterion"])
        assert validated["technical_executability"] == "DETERMINISTICALLY_EXECUTABLE"
        assert validated["usage_authorization"] == "AUTHORIZED_EVALUATOR_INPUT"

        recalculated_result = value(
            await runtime.bus.dispatch(
                request(
                    "criteria/recalculate",
                    "criteria-recalculate",
                    {
                        "project_id": project_id,
                        "criterion_id": criterion_id,
                        "revision_digest": validated["revision_digest"],
                        "variables": {"total": "10", "count": "4"},
                        "input_evidence_refs": span_ids,
                        "evaluator_binding_digest": evaluator_digest,
                    },
                )
            )
        )
        recalculated = cast(dict[str, JsonValue], recalculated_result["criterion"])
        result = cast(dict[str, JsonValue], recalculated["result_and_uncertainty"])
        assert result["value"] == "2.5"

        reference_result = value(
            await runtime.bus.dispatch(
                request(
                    "criteria/reference/generate",
                    "criteria-reference",
                    {
                        "project_id": project_id,
                        "criterion_id": criterion_id,
                        "expected_revision_digest": recalculated["revision_digest"],
                        "source_scope": span_ids,
                        "scenarios": ["reference-only"],
                    },
                )
            )
        )
        reference = cast(dict[str, JsonValue], reference_result["reference"])
        assert reference["authorization_state"] == "NOT_AUTHORIZED"
        assert reference["evaluator_input_allowed"] is False
        reference_id = str(reference["reference_candidate_id"])
        assert isinstance(
            value(
                await runtime.bus.dispatch(
                    request(
                        "criteria/reference/read",
                        "criteria-reference-read",
                        {
                            "project_id": project_id,
                            "reference_candidate_id": reference_id,
                        },
                    )
                )
            )["reference"],
            dict,
        )

        conflict_result = value(
            await runtime.bus.dispatch(
                request(
                    "criteria/field/correct",
                    "criteria-conflict",
                    {
                        "project_id": project_id,
                        "criterion_id": criterion_id,
                        "expected_revision_digest": recalculated["revision_digest"],
                        "field_path": "acceptance_rule.target",
                        "proposed_value": "0.8",
                        "evidence_span_ids": span_ids,
                        "reason": "test conflicting target revision",
                    },
                )
            )
        )
        conflict = cast(dict[str, JsonValue], conflict_result["conflict"])
        assert conflict["status"] == "OPEN"
        conflict_id = str(conflict["conflict_id"])
        assert isinstance(
            value(
                await runtime.bus.dispatch(
                    request(
                        "criteria/conflict/read",
                        "criteria-conflict-read",
                        {"project_id": project_id, "conflict_id": conflict_id},
                    )
                )
            )["conflict"],
            dict,
        )

        changed = cast(dict[str, JsonValue], conflict_result["criterion"])
        proposal = value(
            await runtime.bus.dispatch(
                request(
                    "criteria/change/propose",
                    "criteria-change",
                    {
                        "project_id": project_id,
                        "criterion_id": criterion_id,
                        "expected_revision_digest": changed["revision_digest"],
                        "change_type": "TARGET",
                        "proposed_patch": {"acceptance_rule.target": "0.8"},
                        "evidence_refs": span_ids,
                        "rationale": "official baseline change requires R3 authority",
                    },
                )
            )
        )
        change_proposal = cast(dict[str, JsonValue], proposal["change_proposal"])
        assert change_proposal["state"] == "HUMAN_REQUIRED_R3"
        assert change_proposal["applied"] is False

        listed = value(
            await runtime.bus.dispatch(
                request("criteria/list", "criteria-list", {"project_id": project_id})
            )
        )
        assert len(cast(list[object], listed["criteria"])) == 1
        read = value(
            await runtime.bus.dispatch(
                request(
                    "criteria/read",
                    "criteria-read",
                    {"project_id": project_id, "criterion_id": criterion_id},
                )
            )
        )
        assert isinstance(read["criterion"], dict)
        conflicts = value(
            await runtime.bus.dispatch(
                request(
                    "criteria/conflict/list",
                    "criteria-conflicts",
                    {"project_id": project_id, "criterion_id": criterion_id},
                )
            )
        )
        assert len(cast(list[object], conflicts["conflicts"])) == 1
        references = value(
            await runtime.bus.dispatch(
                request(
                    "criteria/reference/list",
                    "criteria-references",
                    {"project_id": project_id, "criterion_id": criterion_id},
                )
            )
        )
        assert len(cast(list[object], references["references"])) == 1
        audit = value(
            await runtime.bus.dispatch(
                request(
                    "criteria/audit/read",
                    "criteria-audit",
                    {"project_id": project_id, "criterion_id": criterion_id},
                )
            )
        )
        assert len(cast(list[object], audit["records"])) >= 8
    finally:
        runtime.close()
