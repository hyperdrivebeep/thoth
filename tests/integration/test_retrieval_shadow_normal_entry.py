"""S4: a research request replays a 3.0.0 retrieval policy in SHADOW; no model input changes."""

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from tests.integration.behavior_exposure_helpers import prepare_exposure
from tests.integration.paired_evaluation_helpers import PairHarness, pair_harness
from tests.integration.scoped_runtime import fixture_scope_policy
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel
from tests.unit.test_retrieval_selection_oracle import span

from thoth.adapters.storage import ContentAddressedObjectStore
from thoth.application.services import research_retrieval
from thoth.domain.behavior_artifact import BehaviorArtifactKind
from thoth.domain.behavior_policy import RetrievalPolicy
from thoth.domain.enums import ModelRole
from thoth.domain.retrieval_lane import (
    LANE_VERSIONS,
    LEXICAL_SUBSTRING_V1,
    RETRIEVAL_EVALUATOR_ID,
    LaneSelection,
    RetrievalRequest,
    select_retrieval_context,
)

SPANS = (
    span("a1", "alpha one short entry text padded here ok", line=1),
    span("a2", "alpha two a somewhat longer entry text padded here for the test", line=2),
    span("a3", "alpha three the longest of the entries with extra words so it ranks first", line=3),
)
# The baseline keeps the project's usual budgets; only the candidate takes fewer spans.
BASELINE: dict[str, object] = {
    "kind": "RETRIEVAL_POLICY",
    "version": "2.0.0",
    "max_spans": 160,
    "character_budget": 50000,
}
CANDIDATE: dict[str, object] = {
    "kind": "RETRIEVAL_POLICY",
    "version": "3.0.0",
    "max_spans": 1,
    "character_budget": 50000,
    "lanes": [{"lane_id": "LEXICAL_SUBSTRING_V1", "lane_version": "1.0.0"}],
}
CANDIDATE_OUTPUT: dict[str, object] = {
    "selected_refs": ["span:a3"],
    "excluded_count": 2,
    "selected_characters": len(SPANS[2].exact_text),
    "mode": "LEGACY",
    "evaluator": RETRIEVAL_EVALUATOR_ID,
}
CASES: list[dict[str, object]] = [
    {
        "public": {
            "case_id": "retrieval",
            "payload": {"problem": "alpha", "evidence": [s.model_dump(mode="json") for s in SPANS]},
        },
        "expected_output": CANDIDATE_OUTPUT,
        "axis": "quality",
    }
]
QUESTION = "LAB-42의 지연과 조건을 알려줘"
SOURCE = (
    '<html><head><meta property="article:published_time" content="2026-09-01T00:00:00Z" />'
    "</head><body><h1>Latency report</h1><p>단위 ms; 조건 alpha</p>"
    "<p>기록 ID LAB-42: 지연 12 ms</p><p>반증: 다른 조건에서 재현하지 못함</p></body></html>"
)


async def research(h: PairHarness, key: str) -> dict[str, Any]:
    """One research request through the normal RPC entry, settled; returns the published result."""
    started = value(
        await h.runtime.bus.dispatch(
            request(
                "thread/start",
                key,
                {"project_id": h.project, "problem": QUESTION, "contract_version": 2},
            )
        )
    )
    await h.runtime.bus.drain()
    state = value(
        await h.runtime.bus.query(
            request(
                "thread/read",
                key + "-read",
                {
                    "project_id": h.project,
                    "thread_id": started["thread_id"],
                    "view": "FULL",
                    "expected_result_contract": "v5",
                },
            )
        )
    )
    assert state["operation_state"] == "SUCCEEDED", state.get("operation_error")
    return state["current_result"]["result"]


def model_inputs(model: ControlledResearchModel) -> list[tuple[str, tuple[str, ...]]]:
    """Each retrieval-fed model call's role and the span ids it was handed, in call order.

    The memory query expander is left out: it carries no source spans, and it only runs once the
    project already holds memory from an earlier request.
    """
    return [
        (call.role.value, tuple(s.span_id for s in call.context_pack.evidence))
        for call in model.calls
        if call.role != ModelRole.MEMORY_QUERY_EXPANDER
    ]


@dataclass
class Armed:
    h: PairHarness
    exposure: dict[str, Any]
    pair: dict[str, Any]
    # What the same request gave the models before any shadow existed.
    unshadowed: list[tuple[str, tuple[str, ...]]]


@asynccontextmanager
async def armed_shadow(
    tmp_path: Path,
    model: ControlledResearchModel,
    candidate: dict[str, object] = CANDIDATE,
    cases: list[dict[str, object]] = CASES,
) -> AsyncGenerator[Armed]:
    """A project with a source, one plain request, the evaluation pair, and the armed shadow."""
    (tmp_path / "inbox").mkdir(exist_ok=True)
    (tmp_path / "inbox" / "records.html").write_text(SOURCE, encoding="utf-8")
    async with pair_harness(
        tmp_path,
        {},
        {},
        cases,
        policy_pair=(BASELINE, candidate),
        component=BehaviorArtifactKind.RETRIEVAL_POLICY,
        target_component="RETRIEVAL_QUERY_ROUTING",
        executor_id="BEHAVIOR_COMPONENT_SUBPROCESS_V1",
        runtime_options={"model_resolver": model, "resource_scope_policy": fixture_scope_policy()},
    ) as h:
        value(
            await h.runtime.bus.dispatch(
                request(
                    "project/source/connect",
                    "source",
                    {
                        "project_id": h.project,
                        "relative_path": "records.html",
                        "media_type": "text/html",
                        "authority": "INFORMAL",
                        "cutoff_state": "ELIGIBLE",
                        "security_class": "INTERNAL",
                    },
                )
            )
        )
        plain = await research(h, "before-shadow")
        assert all(s["origin"] != "SHADOW" for s in plain["behavior_execution"]["snapshots"])
        assert plain["retrieval_coverage"]["candidate_count"] == 2
        unshadowed = model_inputs(model)
        model.calls.clear()
        exposure, _role, pair = await prepare_exposure(
            h, stage="SHADOW", scope={"environment": "LOCAL"}
        )
        value(
            await h.runtime.bus.dispatch(
                request(
                    "improvement/exposure/start",
                    "arm",
                    {
                        "project_id": h.project,
                        "exposure_id": exposure["spec"]["exposure_id"],
                        "expected_revision": 1,
                    },
                )
            )
        )
        yield Armed(h, exposure, pair, unshadowed)


async def test_a_shadow_retrieval_policy_is_replayed_by_a_research_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = ControlledResearchModel()
    async with armed_shadow(tmp_path, model) as armed:
        h, exposure, pair, unshadowed = armed.h, armed.exposure, armed.pair, armed.unshadowed
        assert pair["performance_verdict"] == "BETTER"
        store = ContentAddressedObjectStore(tmp_path)
        arm = {
            name: json.loads(store.read(pair[name]["output_blob_digest"]))["outputs"][0]
            for name in ("baseline", "candidate")
        }
        assert arm["candidate"] == CANDIDATE_OUTPUT
        assert arm["baseline"]["evaluator"] == RETRIEVAL_EVALUATOR_ID
        assert arm["baseline"]["selected_refs"] == ["span:a3", "span:a2", "span:a1"]

        # Which policy versions the candidate-stage selection function is asked to apply.
        asked: list[str] = []

        def spy(
            request: RetrievalRequest, policy: RetrievalPolicy, lane_id: str | None = None
        ) -> LaneSelection:
            asked.append(policy.version)
            return select_retrieval_context(request, policy, lane_id)

        monkeypatch.setattr(research_retrieval, "select_retrieval_context", spy)
        result = await research(h, "shadow-work")
        monkeypatch.undo()

        # The models get exactly what the active 2.0.0 policy chose; the 1-span shadow changes none.
        assert model_inputs(model) == unshadowed
        assert result["retrieval_coverage"]["candidate_count"] == 2
        execution = result["behavior_execution"]
        shadow = next(s for s in execution["snapshots"] if s["origin"] == "SHADOW")
        active = next(
            s
            for s in execution["snapshots"]
            if s["component"] == "RETRIEVAL_POLICY" and s["origin"] != "SHADOW"
        )
        assert shadow["policy"]["version"] == "3.0.0" and active["policy"]["version"] == "2.0.0"

        # One chain from the evaluation pair to the shadow that a research request ran.
        spec = exposure["spec"]
        assert spec["pair_result_digest"] == pair["result_digest"]
        assert spec["candidate_revision_digest"] == h.proposal["payload"]["candidate_digest"]
        assert shadow["content_digest"] == spec["candidate_digest"]

        # The shadow was replayed on the very requests the active policy served (same input
        # digest) and chose differently, which only the shadow's own policy can explain. The
        # active policy also journals the context assembly, which no shadow replays.
        def selections(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
            return [
                u for u in execution["uses"] if u["snapshot_digest"] == snapshot["snapshot_digest"]
            ]

        replayed, served = selections(shadow), {u["input_digest"]: u for u in selections(active)}
        # Every candidate selection of the active 2.0.0 policy was followed by one of the same
        # function under the 3.0.0 shadow policy, and each was journaled against the shadow.
        assert asked.count("3.0.0") > 0 and asked.count("3.0.0") == asked.count("2.0.0")
        assert len(replayed) >= asked.count("3.0.0")
        assert replayed and all(u["operation"] == "EVIDENCE_SELECTION" for u in replayed)
        assert all(u["input_digest"] in served for u in replayed)
        assert any(
            u["output_digest"] != served[u["input_digest"]]["output_digest"] for u in replayed
        )

        current = value(
            await h.runtime.bus.dispatch(
                request(
                    "improvement/exposure/runtime/read",
                    "shadow-read",
                    {"project_id": h.project, "exposure_id": spec["exposure_id"]},
                )
            )
        )["exposure"]
        (observation,) = current["observations"]
        assert observation["consumed"] is True and observation["successful"] is True
        assert observation["decision_exposed"] is False and observation["model_calls"] == 0
        assert any(
            item["stage"] == "SHADOW" and item["status"] == "EXECUTED"
            for item in current["stage_evidence"]
        )


async def test_a_shadow_that_cannot_select_is_held_and_the_request_still_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = ControlledResearchModel()
    async with armed_shadow(tmp_path, model) as armed:
        h = armed.h
        # The 3.0.0 shadow names lane version 1.0.0; the code now registers another one. The
        # active 2.0.0 policy names no lane version, so only the shadow is refused.
        monkeypatch.setitem(LANE_VERSIONS, LEXICAL_SUBSTRING_V1, "9.9.9")
        result = await research(h, "held-shadow")
        monkeypatch.undo()

        assert model_inputs(model) == armed.unshadowed
        assert result["retrieval_coverage"]["candidate_count"] == 2
        execution = result["behavior_execution"]
        shadow = next(s for s in execution["snapshots"] if s["origin"] == "SHADOW")
        uses = [u for u in execution["uses"] if u["snapshot_digest"] == shadow["snapshot_digest"]]
        # The candidate stage is held; the adjacent-packet replay does not go through the
        # selection function and is journaled as before.
        held = [u for u in uses if u["operation"] == "POLICY_HELD"]
        assert held and {u["operation"] for u in uses} <= {"POLICY_HELD", "EVIDENCE_SELECTION"}
        assert any(u["operation"] == "EVIDENCE_SELECTION" for u in execution["uses"])

        current = value(
            await h.runtime.bus.dispatch(
                request(
                    "improvement/exposure/runtime/read",
                    "held-read",
                    {"project_id": h.project, "exposure_id": armed.exposure["spec"]["exposure_id"]},
                )
            )
        )["exposure"]
        (observation,) = current["observations"]
        assert observation["consumed"] is True and observation["successful"] is False
        assert current["state"] == "ROLLED_BACK"
