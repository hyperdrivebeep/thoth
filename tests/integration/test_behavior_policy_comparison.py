"""The comparison worker and normal consumers share the same policy functions."""

import json
from pathlib import Path

import pytest
from tests.integration.paired_evaluation_helpers import pair_harness

from thoth.adapters.storage import ContentAddressedObjectStore
from thoth.domain.behavior_artifact import BehaviorArtifactKind


@pytest.mark.parametrize(
    "kind,target,baseline,candidate,payload,expected",
    [
        (
            BehaviorArtifactKind.PROMPT_BUNDLE,
            "PROMPT",
            {"task_guidance": "old"},
            {"task_guidance": "cite sources"},
            {},
            {"behavior_guidance": "cite sources"},
        ),
        (
            BehaviorArtifactKind.WORKFLOW_DEFINITION,
            "WORKFLOW_GATE_ORDER",
            {"max_semantic_repairs": 0},
            {"max_semantic_repairs": 1},
            {"needs_repair": True},
            {"decision": "REPAIR"},
        ),
    ],
)
async def test_registered_policy_runs_in_both_real_comparison_workspaces(
    tmp_path: Path,
    kind: BehaviorArtifactKind,
    target: str,
    baseline: dict[str, object],
    candidate: dict[str, object],
    payload: dict[str, object],
    expected: dict[str, object],
) -> None:
    policies = (
        {"kind": kind.value, "version": "2.0.0", **baseline},
        {"kind": kind.value, "version": "2.0.0", **candidate},
    )
    cases: list[dict[str, object]] = [
        {
            "public": {"case_id": "policy", "payload": payload},
            "expected_output": expected,
            "axis": "quality",
        }
    ]
    async with pair_harness(
        tmp_path,
        {},
        {},
        cases,
        policy_pair=policies,
        component=kind,
        target_component=target,
        executor_id="BEHAVIOR_COMPONENT_SUBPROCESS_V1",
    ) as h:
        response = await h.run()
        assert response["state"] == "COMPLETE", response
        result = response["pair"]["result"]
        assert result["performance_verdict"] == "BETTER"
        assert result["baseline"]["workspace_id"] != result["candidate"]["workspace_id"]
        assert result["baseline"]["output_blob_digest"] != result["candidate"]["output_blob_digest"]
        actual = json.loads(
            ContentAddressedObjectStore(tmp_path).read(result["candidate"]["output_blob_digest"])
        )
        assert actual["outputs"] == [expected]
        assert result["promotion_eligible"] is False
