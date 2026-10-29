"""Policy 5 stays a shadow; real research still serves its immutable 2.0 baseline."""

from pathlib import Path

import pytest
from tests.integration.behavior_exposure_helpers import prepare_exposure
from tests.integration.paired_evaluation_helpers import pair_harness
from tests.integration.test_research_request_v2 import ControlledResearchModel
from tests.integration.test_retrieval_shadow_normal_entry import (
    BASELINE,
    CANDIDATE_OUTPUT,
    CASES,
    armed_shadow,
    model_inputs,
    research,
)

from thoth.domain.behavior_artifact import BehaviorArtifactKind
from thoth.domain.behavior_policy import RetrievalRoutingPolicy

POLICY = RetrievalRoutingPolicy(max_spans=1).model_dump(mode="json")
ROUTING = {
    "artifact_count": 1,
    "route": "LEXICAL",
    "dense_used": False,
    "fallback_reason": None,
    "dense_material_digest": None,
}
FIXTURES = [{**CASES[0], "expected_output": {**CANDIDATE_OUTPUT, "routing": ROUTING}}]


async def test_policy5_shadow_normal_entry_keeps_model_inputs(tmp_path: Path) -> None:
    model = ControlledResearchModel()
    async with armed_shadow(tmp_path, model, POLICY, FIXTURES) as armed:
        result = await research(armed.h, "routed-shadow")
        assert model_inputs(model) == armed.unshadowed
        execution = result["behavior_execution"]
        assert any(
            s["origin"] == "SHADOW" and s["policy"]["version"] == "5.0.0"
            for s in execution["snapshots"]
        )
        assert all(
            s["policy"]["version"] == "2.0.0"
            for s in execution["snapshots"]
            if s["origin"] != "SHADOW" and s["component"] == "RETRIEVAL_POLICY"
        )
        shadow = next(s for s in execution["snapshots"] if s["origin"] == "SHADOW")
        assert any(
            u["snapshot_digest"] == shadow["snapshot_digest"]
            and u["operation"] == "EVIDENCE_SELECTION"
            for u in execution["uses"]
        )


async def test_policy5_even_a_better_pair_cannot_prepare_canary(tmp_path: Path) -> None:
    async with pair_harness(
        tmp_path,
        {},
        {},
        FIXTURES,
        policy_pair=(BASELINE, POLICY),
        component=BehaviorArtifactKind.RETRIEVAL_POLICY,
        target_component="RETRIEVAL_QUERY_ROUTING",
        executor_id="BEHAVIOR_COMPONENT_SUBPROCESS_V1",
    ) as h:
        with pytest.raises(AssertionError, match="RETRIEVAL_ROUTING_SHADOW_ONLY"):
            await prepare_exposure(h, stage="CANARY", scope={"environment": "LOCAL"})
