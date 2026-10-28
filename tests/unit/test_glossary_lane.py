"""G3 2: the glossary lane ranks structure groups by the English terms a Korean question stands for.

The term list is a small invented one. The report is the one the other ranked-lane tests use.
"""

from dataclasses import replace
from typing import Any

import pytest
from pydantic import ValidationError
from tests.unit.test_ranked_lanes import report

from thoth.domain.behavior_policy import (
    GlossaryPin,
    RetrievalFusionPolicy,
    RetrievalLanePolicy,
)
from thoth.domain.glossary import (
    GLOSSARY_MATCHER_VERSION,
    Glossary,
    GlossaryConfidence,
    build_glossary,
    make_entry,
)
from thoth.domain.query_identifiers import SCANNER_VERSION
from thoth.domain.retrieval_lane import (
    GLOSSARY_BM25,
    GROUPED_BM25,
    LANE_PROVENANCE,
    LANE_VERSIONS,
    PROTECTED_EXACT,
    RetrievalRequest,
    glossary_bm25_chunks_v1,
    select_retrieval_context,
)
from thoth.domain.structure_groups import GROUPING_VERSION

ROWS: tuple[tuple[str, str, str, GlossaryConfidence], ...] = (
    ("중앙값", "median", "perf", "HIGH"),
    ("실행 시간", "time per run", "perf", "MEDIUM"),
    ("성능", "performance", "perf", "HIGH"),
    ("연결", "connection", "db", "HIGH"),
    ("연결", "linking", "compiler", "MEDIUM"),
)


def glossary() -> Glossary:
    return build_glossary([make_entry(*row) for row in ROWS])


def lane_spec(lane: str, depth: int, floor: GlossaryConfidence) -> dict[str, Any]:
    spec: dict[str, Any] = {"lane_id": lane, "depth": depth}
    if lane == GLOSSARY_BM25:
        spec["glossary"] = {"digest": glossary().digest, "min_confidence": floor}
    return spec


def policy(
    *lanes: tuple[str, int], floor: GlossaryConfidence = "MEDIUM", max_spans: int = 6
) -> RetrievalFusionPolicy:
    return RetrievalFusionPolicy.model_validate(
        {"max_spans": max_spans, "lanes": [lane_spec(lane, depth, floor) for lane, depth in lanes]}
    )


def request(question: str, held: Glossary | None = None) -> RetrievalRequest:
    spans, catalog = report()
    return RetrievalRequest(question, (), spans, (), catalog, held)


def test_a_korean_question_finds_english_groups_the_other_lanes_cannot() -> None:
    chosen = select_retrieval_context(
        request("중앙값이 뭐야", glossary()), policy((GLOSSARY_BM25, 3), (GROUPED_BM25, 3))
    )
    assert any("median" in span.exact_text for span in chosen.selected)
    glossary_lane, original_lane = chosen.contributions
    assert glossary_lane.lane_id == GLOSSARY_BM25 and glossary_lane.credited_refs
    # The question has no English word, so the original lane ranks nothing: the credit is the
    # glossary lane's alone.
    assert original_lane.ranked == 0 and original_lane.credited_refs == ()


def test_the_lane_is_ranked_by_the_expansion_alone_not_by_the_words_of_the_question() -> None:
    spans, _catalog = report()
    ready = {"glossary_min_confidence": "MEDIUM"}
    plain = replace(request("중앙값이 뭐야", glossary()), **ready)
    # An English word in the question that is not an expansion changes nothing for this lane.
    mixed = replace(request("중앙값이 뭐야 version keys", glossary()), **ready)
    first = glossary_bm25_chunks_v1(plain, spans)
    assert first and first == glossary_bm25_chunks_v1(mixed, spans)


def test_a_question_with_nothing_to_expand_gives_the_lane_nothing() -> None:
    for question in ("what is the median", "연결을 설정해줘", "아무 용어도 없는 질문"):
        chosen = select_retrieval_context(
            request(question, glossary()), policy((GLOSSARY_BM25, 3), (PROTECTED_EXACT, 3))
        )
        lane = chosen.contributions[0]
        assert lane.lane_id == GLOSSARY_BM25
        assert lane.ranked == 0 and lane.credited_refs == () and lane.also_refs == ()


def test_the_confidence_floor_of_the_policy_decides_what_is_expanded() -> None:
    question = "실행 시간의 중앙값"
    both = select_retrieval_context(
        request(question, glossary()), policy((GLOSSARY_BM25, 3), (GROUPED_BM25, 3))
    )
    high = select_retrieval_context(
        request(question, glossary()), policy((GLOSSARY_BM25, 3), (GROUPED_BM25, 3), floor="HIGH")
    )
    assert both.glossary is not None and high.glossary is not None
    assert both.glossary.matches.expansions == ("time per run", "median")
    assert high.glossary.matches.expansions == ("median",)


def test_a_glossary_of_another_digest_or_none_at_all_refuses_the_selection() -> None:
    wrong = build_glossary([make_entry("중앙값", "average", "perf", "HIGH")])
    pair = policy((GLOSSARY_BM25, 3), (GROUPED_BM25, 3))
    with pytest.raises(ValueError, match="RETRIEVAL_GLOSSARY_DIGEST_MISMATCH"):
        select_retrieval_context(request("중앙값", wrong), pair)
    with pytest.raises(ValueError, match="RETRIEVAL_GLOSSARY_UNAVAILABLE"):
        select_retrieval_context(request("중앙값", None), pair)
    # Also when the whole catalog would have fitted: a policy is refused, not half used.
    roomy = policy((GLOSSARY_BM25, 3), (GROUPED_BM25, 3), max_spans=160)
    with pytest.raises(ValueError, match="RETRIEVAL_GLOSSARY_DIGEST_MISMATCH"):
        select_retrieval_context(request("중앙값", wrong), roomy)
    with pytest.raises(ValueError, match="RETRIEVAL_GLOSSARY_UNAVAILABLE"):
        select_retrieval_context(request("중앙값", None), roomy)


def test_the_lane_run_on_its_own_needs_both_a_glossary_and_a_floor() -> None:
    spans, _catalog = report()
    bare = request("중앙값이 뭐야", glossary())
    for lacking in (replace(bare, glossary=None), bare):
        # The frame hands the lane the policy's floor; with no floor the lane does not pick one.
        with pytest.raises(ValueError, match="RETRIEVAL_GLOSSARY_UNAVAILABLE"):
            glossary_bm25_chunks_v1(lacking, spans)


def test_a_policy_without_the_glossary_lane_does_not_read_the_glossary() -> None:
    other = policy((GROUPED_BM25, 3), (PROTECTED_EXACT, 3))
    without = select_retrieval_context(request("v2 keys median"), other)
    with_one = select_retrieval_context(request("v2 keys median", glossary()), other)
    assert without.selected == with_one.selected
    assert without.provenance == with_one.provenance and with_one.glossary is None


def test_the_selection_records_the_glossary_and_what_was_used() -> None:
    chosen = select_retrieval_context(
        request("연결과 중앙값", glossary()), policy((GLOSSARY_BM25, 3), (GROUPED_BM25, 3))
    )
    assert chosen.glossary is not None
    record = dict(chosen.provenance)
    assert record["lane0"] == "GLOSSARY_BM25:1.0.0:depth=3"
    assert record["lane0.matcher"] == GLOSSARY_MATCHER_VERSION
    assert record["lane0.glossary_digest"] == glossary().digest
    assert record["lane0.min_confidence"] == "MEDIUM"
    assert record["lane0.glossary_entries"] == str(len(ROWS))
    assert record["lane0.glossary_verified"] == "true"
    (fired,) = chosen.glossary.matches.fired
    assert record["lane0.fired"] == ",".join(fired.entry_ids)
    assert [a.matched for a in chosen.glossary.matches.abstained] == ["연결"]
    # The lane after it has no glossary keys.
    assert not any(key.startswith("lane1.glossary") for key in record)


def test_the_lane_is_registered_with_its_rules() -> None:
    assert LANE_VERSIONS[GLOSSARY_BM25] == "1.0.0"
    assert dict(LANE_PROVENANCE[GLOSSARY_BM25]) == {
        "matcher": GLOSSARY_MATCHER_VERSION,
        "grouping": GROUPING_VERSION,
        "scoring": dict(LANE_PROVENANCE[GROUPED_BM25])["scoring"],
        "scanner": SCANNER_VERSION,
    }


def test_the_glossary_lane_belongs_to_policy_4_0_0_with_a_pin_and_nowhere_else() -> None:
    pin = GlossaryPin(digest=glossary().digest, min_confidence="HIGH")
    assert pin.min_confidence == "HIGH"
    grouped = lane_spec(GROUPED_BM25, 3, "HIGH")
    own = lane_spec(GLOSSARY_BM25, 3, "HIGH")
    assert RetrievalFusionPolicy.model_validate({"lanes": [grouped, own]})
    digest = glossary().digest
    bad_lanes: list[list[dict[str, Any]]] = [
        # The glossary lane without a pin, another lane with one.
        [grouped, {"lane_id": GLOSSARY_BM25, "depth": 3}],
        [{**grouped, "glossary": own["glossary"]}, lane_spec(PROTECTED_EXACT, 1, "HIGH")],
        # A digest that is not a digest, no floor, a floor that does not exist.
        [grouped, {**own, "glossary": {"digest": "abc", "min_confidence": "HIGH"}}],
        [grouped, {**own, "glossary": {"digest": digest}}],
        [grouped, {**own, "glossary": {"digest": digest, "min_confidence": "LOW"}}],
    ]
    for lanes in bad_lanes:
        with pytest.raises(ValidationError):
            RetrievalFusionPolicy.model_validate({"lanes": lanes})
    # Policy 3.0.0 is frozen: it does not know the glossary lane.
    with pytest.raises(ValidationError):
        RetrievalLanePolicy.model_validate({"lanes": [{"lane_id": GLOSSARY_BM25}]})


def test_a_lane_without_a_glossary_keeps_its_serialized_form() -> None:
    plain = RetrievalFusionPolicy.model_validate(
        {"lanes": [lane_spec(GROUPED_BM25, 3, "HIGH"), lane_spec(PROTECTED_EXACT, 12, "HIGH")]}
    )
    assert plain.model_dump(mode="json")["lanes"] == [
        {"lane_id": "GROUPED_BM25", "lane_version": "1.0.0", "depth": 3},
        {"lane_id": "PROTECTED_EXACT", "lane_version": "1.0.0", "depth": 12},
    ]
    first, second = policy((GLOSSARY_BM25, 3), (GROUPED_BM25, 3), floor="HIGH").model_dump(
        mode="json"
    )["lanes"]
    assert first["glossary"] == {"digest": glossary().digest, "min_confidence": "HIGH"}
    assert "glossary" not in second
