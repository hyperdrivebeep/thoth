"""G3 1c: a research request replays a 4.0.0 (fused lanes) retrieval policy in SHADOW.

The same chain as the 3.0.0 test, with a candidate that merges two lanes: an evaluation pair, an
exposure resting on it, a research request through the normal entry, and the replayed shadow. The
models get what the active 2.0.0 policy chose; a shadow that is refused or that breaks is held.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel
from tests.integration.test_retrieval_shadow_normal_entry import (
    CASES,
    SPANS,
    armed_shadow,
    model_inputs,
    research,
)

from thoth.adapters.storage import ContentAddressedObjectStore
from thoth.application.services import research_retrieval
from thoth.domain.behavior_policy import RetrievalPolicy
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.retrieval_lane import (
    LANE_VERSIONS,
    LANES,
    PROTECTED_EXACT,
    RETRIEVAL_EVALUATOR_ID,
    Lane,
    LaneSelection,
    RetrievalRequest,
    select_retrieval_context,
)

FUSED: dict[str, object] = {
    "kind": "RETRIEVAL_POLICY",
    "version": "4.0.0",
    "max_spans": 1,
    "character_budget": 50000,
    "lanes": [
        {"lane_id": "PROTECTED_EXACT", "lane_version": "1.0.0", "depth": 2},
        {"lane_id": "LEXICAL_SUBSTRING_V1", "lane_version": "1.0.0", "depth": 2},
    ],
    "fusion": {"method_id": "ROUND_ROBIN_DEDUP", "method_version": "1.0.0"},
}
FUSED_OUTPUT: dict[str, object] = {
    "selected_refs": ["span:a1"],
    "excluded_count": 2,
    "selected_characters": len(SPANS[0].exact_text),
    "mode": "LEGACY",
    "evaluator": RETRIEVAL_EVALUATOR_ID,
    "lane": {
        "fusion": "ROUND_ROBIN_DEDUP",
        "fusion_version": "1.0.0",
        "lane0": "PROTECTED_EXACT:1.0.0:depth=2",
        "lane0.scanner": "query-identifiers:1.0.0",
        "lane1": "LEXICAL_SUBSTRING_V1:1.0.0:depth=2",
    },
    "groups": 0,
    "lanes": [
        {
            "lane_id": "PROTECTED_EXACT",
            "lane_version": "1.0.0",
            "depth": 2,
            "ranked": 3,
            "credited_refs": ["span:a1"],
            "also_refs": [],
        },
        {
            "lane_id": "LEXICAL_SUBSTRING_V1",
            "lane_version": "1.0.0",
            "depth": 2,
            "ranked": 3,
            "credited_refs": [],
            "also_refs": [],
        },
    ],
}
FUSED_CASES: list[dict[str, object]] = [{**CASES[0], "expected_output": FUSED_OUTPUT}]
FAILED_OUTPUT = domain_digest(
    "BEHAVIOR_POLICY_HELD", "1.0.0", canonical_payload({"reason_code": "SHADOW_SELECTION_FAILED"})
)


def shadow_uses(execution: dict[str, Any]) -> list[dict[str, Any]]:
    shadow = next(s for s in execution["snapshots"] if s["origin"] == "SHADOW")
    return [u for u in execution["uses"] if u["snapshot_digest"] == shadow["snapshot_digest"]]


async def observation(armed: Any, key: str) -> dict[str, Any]:
    current = value(
        await armed.h.runtime.bus.dispatch(
            request(
                "improvement/exposure/runtime/read",
                key,
                {
                    "project_id": armed.h.project,
                    "exposure_id": armed.exposure["spec"]["exposure_id"],
                },
            )
        )
    )["exposure"]
    return current


async def test_a_fused_shadow_is_evaluated_and_then_replayed_by_a_research_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = ControlledResearchModel()
    async with armed_shadow(tmp_path, model, FUSED, FUSED_CASES) as armed:
        pair = armed.pair
        assert pair["performance_verdict"] == "BETTER"
        store = ContentAddressedObjectStore(tmp_path)
        arms = {
            name: json.loads(store.read(pair[name]["output_blob_digest"]))["outputs"][0]
            for name in ("baseline", "candidate")
        }
        # The evaluation recorded who gave what; the 2.0.0 baseline output has none of those keys.
        assert arms["candidate"] == FUSED_OUTPUT
        assert "lanes" not in arms["baseline"] and "groups" not in arms["baseline"]

        asked: list[str] = []

        def spy(
            req: RetrievalRequest, policy: RetrievalPolicy, lane_id: str | None = None
        ) -> LaneSelection:
            asked.append(policy.version)
            return select_retrieval_context(req, policy, lane_id)

        monkeypatch.setattr(research_retrieval, "select_retrieval_context", spy)
        result = await research(armed.h, "fused-work")
        monkeypatch.undo()

        assert model_inputs(model) == armed.unshadowed
        execution = result["behavior_execution"]
        shadow = next(s for s in execution["snapshots"] if s["origin"] == "SHADOW")
        assert shadow["policy"]["version"] == "4.0.0"
        assert shadow["policy"]["lanes"][0]["depth"] == 2
        assert asked.count("4.0.0") > 0 and asked.count("4.0.0") == asked.count("2.0.0")
        replayed = shadow_uses(execution)
        assert replayed and all(u["operation"] == "EVIDENCE_SELECTION" for u in replayed)
        current = await observation(armed, "fused-read")
        (seen,) = current["observations"]
        assert seen["consumed"] is True and seen["successful"] is True
        assert seen["decision_exposed"] is False and seen["model_calls"] == 0


async def test_a_fused_shadow_naming_an_unregistered_lane_version_is_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = ControlledResearchModel()
    async with armed_shadow(tmp_path, model, FUSED, FUSED_CASES) as armed:
        monkeypatch.setitem(LANE_VERSIONS, PROTECTED_EXACT, "9.9.9")
        result = await research(armed.h, "fused-held")
        monkeypatch.undo()
        assert model_inputs(model) == armed.unshadowed
        uses = shadow_uses(result["behavior_execution"])
        assert any(u["operation"] == "POLICY_HELD" for u in uses)
        current = await observation(armed, "fused-held-read")
        (seen,) = current["observations"]
        assert seen["consumed"] is True and seen["successful"] is False


async def test_a_fused_shadow_whose_lane_breaks_is_held_as_failed_and_the_request_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = ControlledResearchModel()
    async with armed_shadow(tmp_path, model, FUSED, FUSED_CASES) as armed:

        def broken(req: RetrievalRequest, eligible: tuple[Any, ...]) -> tuple[Any, ...]:
            raise RuntimeError("lane defect")

        lane: Lane = broken
        monkeypatch.setitem(LANES, PROTECTED_EXACT, lane)
        result = await research(armed.h, "fused-failed")
        monkeypatch.undo()
        assert model_inputs(model) == armed.unshadowed
        uses = shadow_uses(result["behavior_execution"])
        held = [u for u in uses if u["operation"] == "POLICY_HELD"]
        assert held and {u["output_digest"] for u in held} == {FAILED_OUTPUT}
        current = await observation(armed, "fused-failed-read")
        (seen,) = current["observations"]
        assert seen["consumed"] is True and seen["successful"] is False
