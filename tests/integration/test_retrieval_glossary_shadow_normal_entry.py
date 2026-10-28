"""G3 2: a research request replays a glossary-lane shadow from the workspace's term list.

The same chain as the fused-lane test: an evaluation pair whose case carries the glossary entries it
needs, an exposure resting on it, a research request through the normal entry, and the replayed
shadow. The term list is a small invented file in the workspace; the models get what the active
2.0.0 policy chose. A shadow that has no list, or a list of another digest, is held.
"""

import json
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import JsonValue
from tests.integration.test_research_request_v2 import ControlledResearchModel
from tests.integration.test_retrieval_fusion_shadow_normal_entry import (
    observation,
    shadow_uses,
)
from tests.integration.test_retrieval_shadow_normal_entry import (
    armed_shadow,
    model_inputs,
    research,
)
from tests.unit.test_retrieval_selection_oracle import span

from thoth.adapters.behavior.registry import evaluate_retrieval
from thoth.adapters.storage import ContentAddressedObjectStore
from thoth.application.services import research_retrieval
from thoth.domain.behavior_policy import RetrievalFusionPolicy, RetrievalPolicy
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.glossary import GlossaryConfidence, build_glossary, make_entry
from thoth.domain.retrieval_case import evaluation_case_payload
from thoth.domain.retrieval_lane import (
    LaneSelection,
    RetrievalRequest,
    select_retrieval_context,
)

# The question of the shared research request is "LAB-42의 지연과 조건을 알려줘".
ROWS: tuple[tuple[str, str, str, GlossaryConfidence], ...] = (
    ("지연", "latency", "perf", "HIGH"),
    ("조건", "condition", "perf", "MEDIUM"),
)
CSV = "ko,en,domain,source,confidence\n" + "".join(
    f"{ko},{en},{domain},invented,{confidence}\n" for ko, en, domain, confidence in ROWS
)
OTHER_CSV = "ko,en,domain,confidence\n지연,delay,perf,HIGH\n"
LIST_SPANS = (
    span("a1", "latency of the run was twelve ms in this measurement", line=1),
    span("a2", "alpha two an unrelated entry text padded here ok", line=2),
    span("a3", "alpha three another unrelated entry with extra words", line=3),
)
LIST = build_glossary([make_entry(*row) for row in ROWS])
SHADOW_POLICY: dict[str, object] = {
    "kind": "RETRIEVAL_POLICY",
    "version": "4.0.0",
    "max_spans": 1,
    "character_budget": 50000,
    "lanes": [
        {
            "lane_id": "GLOSSARY_BM25",
            "lane_version": "1.0.0",
            "depth": 1,
            "glossary": {"digest": LIST.digest, "min_confidence": "MEDIUM"},
        },
        {"lane_id": "PROTECTED_EXACT", "lane_version": "1.0.0", "depth": 1},
    ],
    "fusion": {"method_id": "ROUND_ROBIN_DEDUP", "method_version": "1.0.0"},
}
HELD_OUTPUT = domain_digest(
    "BEHAVIOR_POLICY_HELD", "1.0.0", canonical_payload({"reason_code": "SHADOW_SELECTION_HELD"})
)


def case_payload() -> dict[str, JsonValue]:
    asked = RetrievalRequest("지연이 얼마야", (), LIST_SPANS, (), None, LIST)
    return evaluation_case_payload(asked)


def cases() -> list[dict[str, object]]:
    """The evaluation case, with the output the candidate policy gives it."""
    payload = case_payload()
    policy = RetrievalFusionPolicy.model_validate(SHADOW_POLICY)
    expected = evaluate_retrieval(policy, payload)
    return [
        {
            "public": {"case_id": "glossary", "payload": payload},
            "expected_output": expected,
            "axis": "quality",
        }
    ]


def put_list(workspace: Path, text: str, *, bom: bool = False) -> None:
    folder = workspace / "retrieval-glossary"
    folder.mkdir(exist_ok=True)
    (folder / "glossary.csv").write_bytes((b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8"))


async def test_a_glossary_shadow_is_evaluated_and_replayed_from_the_workspace_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    put_list(tmp_path, CSV, bom=True)
    model = ControlledResearchModel()
    async with armed_shadow(tmp_path, model, SHADOW_POLICY, cases()) as armed:
        pair = armed.pair
        assert pair["performance_verdict"] == "BETTER"
        store = ContentAddressedObjectStore(tmp_path)
        arms = {
            name: json.loads(store.read(pair[name]["output_blob_digest"]))["outputs"][0]
            for name in ("baseline", "candidate")
        }
        lanes = cast(list[dict[str, Any]], arms["candidate"]["lanes"])
        assert arms["candidate"]["selected_refs"] == ["span:a1"]
        record = lanes[0]["glossary"]
        assert record["digest"] == LIST.digest and record["digest_verified"] is False
        assert record["entries_carried"] == 1 and record["expansions"] == ["latency"]
        assert "lanes" not in arms["baseline"]

        seen: list[LaneSelection] = []

        def spy(
            req: RetrievalRequest, policy: RetrievalPolicy, lane_id: str | None = None
        ) -> LaneSelection:
            chosen = select_retrieval_context(req, policy, lane_id)
            if policy.version == "4.0.0":
                seen.append(chosen)
            return chosen

        monkeypatch.setattr(research_retrieval, "select_retrieval_context", spy)
        result = await research(armed.h, "glossary-work")
        monkeypatch.undo()

        assert model_inputs(model) == armed.unshadowed
        # The shadow read the workspace list: it is verified, whole, and found a term in the
        # Korean question of the request.
        assert seen
        for chosen in seen:
            assert chosen.glossary is not None and chosen.glossary.verified
            assert chosen.glossary.digest == LIST.digest and chosen.glossary.entries_carried == 2
            assert chosen.glossary.matches.expansions == ("latency", "condition")
        replayed = shadow_uses(result["behavior_execution"])
        assert replayed and all(u["operation"] == "EVIDENCE_SELECTION" for u in replayed)
        current = await observation(armed, "glossary-read")
        (observed,) = current["observations"]
        assert observed["consumed"] is True and observed["successful"] is True
        assert observed["decision_exposed"] is False and observed["model_calls"] == 0


@pytest.mark.parametrize("text", [None, OTHER_CSV, "ko,en\n지연,latency\n"])
async def test_a_glossary_shadow_without_its_list_is_held_and_the_request_succeeds(
    tmp_path: Path, text: str | None
) -> None:
    # No file, a list of another digest, and a file that cannot be used all hold the shadow.
    if text is not None:
        put_list(tmp_path, text)
    model = ControlledResearchModel()
    async with armed_shadow(tmp_path, model, SHADOW_POLICY, cases()) as armed:
        result = await research(armed.h, "glossary-held")
        assert model_inputs(model) == armed.unshadowed
        uses = shadow_uses(result["behavior_execution"])
        held = [u for u in uses if u["operation"] == "POLICY_HELD"]
        assert held and {u["output_digest"] for u in held} == {HELD_OUTPUT}
        current = await observation(armed, "glossary-held-read")
        (observed,) = current["observations"]
        assert observed["consumed"] is True and observed["successful"] is False
