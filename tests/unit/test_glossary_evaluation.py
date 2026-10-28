"""G3 2: an evaluation case carries the glossary it needs, and the evaluator checks what it can.

The term list is a small invented one (see test_glossary_lane). A case carries the digest of the
whole list and only the entries whose Korean term occurs in its texts; the digest is claimed, not
recomputed, and the evaluation output says so.
"""

import json
from typing import cast

import pytest
from pydantic import JsonValue, ValidationError
from tests.unit.test_glossary_lane import ROWS, glossary, policy
from tests.unit.test_ranked_lanes import report

from thoth.adapters.behavior.registry import evaluate_retrieval
from thoth.domain.behavior_policy import RetrievalBehaviorInput
from thoth.domain.glossary import (
    Glossary,
    GlossaryConfidence,
    build_glossary,
    make_entry,
    match_glossary,
)
from thoth.domain.retrieval_case import evaluation_case_payload, request_of_case
from thoth.domain.retrieval_lane import (
    GLOSSARY_BM25,
    GROUPED_BM25,
    PROTECTED_EXACT,
    RetrievalRequest,
    select_retrieval_context,
)

QUESTIONS = (
    "중앙값이 뭐야",
    "연결과 중앙값",
    "실행시간의 중앙값",
    "실행 시간을 줄여줘",
    "성 능 은 아님",
    "what is the median",
    "v2 keys 성능입니다",
)


def request(
    question: str, queries: tuple[str, ...] = (), held: Glossary | None = None
) -> RetrievalRequest:
    spans, catalog = report()
    return RetrievalRequest(question, queries, spans, (), catalog, held or glossary())


def frozen(item: RetrievalRequest) -> dict[str, JsonValue]:
    """The payload as a host would freeze it: written to JSON text and read back."""
    return cast(dict[str, JsonValue], json.loads(json.dumps(evaluation_case_payload(item))))


def carried(payload: dict[str, JsonValue]) -> list[dict[str, JsonValue]]:
    held = cast(dict[str, JsonValue], payload["glossary"])
    return cast(list[dict[str, JsonValue]], held["entries"])


def lane_record(output: dict[str, JsonValue], lane_id: str) -> dict[str, JsonValue]:
    lanes = cast(list[dict[str, JsonValue]], output["lanes"])
    (found,) = [lane for lane in lanes if lane["lane_id"] == lane_id]
    return found


def test_a_case_carries_the_digest_and_only_the_entries_that_can_matter() -> None:
    payload = frozen(request("중앙값이 뭐야"))
    held = cast(dict[str, JsonValue], payload["glossary"])
    assert held["digest"] == glossary().digest
    assert [e["ko"] for e in carried(payload)] == ["중앙값"]
    # Both translations of an ambiguous term travel together, so ambiguity is judged as before.
    both = frozen(request("연결과 중앙값"))
    assert sorted(str(e["en"]) for e in carried(both)) == ["connection", "linking", "median"]
    assert carried(frozen(request("what is the median"))) == []


def test_the_entries_a_case_carries_reproduce_every_match_through_the_round_trip() -> None:
    whole = glossary()
    for question in QUESTIONS:
        for queries in ((), ("실행 시간 연결",)):
            item = request(question, queries)
            case = RetrievalBehaviorInput.model_validate(frozen(item))
            rebuilt = request_of_case(case).glossary
            assert rebuilt is not None and rebuilt.digest == whole.digest
            assert not rebuilt.verified and len(rebuilt.entries) <= len(ROWS)
            texts = (question, *queries)
            for floor in ("HIGH", "MEDIUM"):
                assert match_glossary(texts, rebuilt, floor) == match_glossary(texts, whole, floor)


@pytest.mark.parametrize("floor", ["HIGH", "MEDIUM"])
def test_evaluating_a_case_selects_what_the_request_itself_selects(floor: str) -> None:
    level = cast(GlossaryConfidence, floor)
    fused = policy((GLOSSARY_BM25, 3), (GROUPED_BM25, 3), (PROTECTED_EXACT, 6), floor=level)
    for question in QUESTIONS:
        item = request(question, ("실행 시간",))
        direct = select_retrieval_context(item, fused)
        output = evaluate_retrieval(fused, frozen(item))
        assert output["selected_refs"] == [s.span_id for s in direct.selected], question
        # Only these two keys differ: a case carries a part of the list and its digest is claimed.
        differs = ("lane0.glossary_entries", "lane0.glossary_verified")
        lane = cast(dict[str, JsonValue], output["lane"])
        assert {k: v for k, v in lane.items() if k not in differs} == {
            k: v for k, v in direct.provenance if k not in differs
        }, question
        assert lane["lane0.glossary_verified"] == "false"
        assert dict(direct.provenance)["lane0.glossary_verified"] == "true"
        assert direct.glossary is not None and direct.glossary.verified
        record = lane_record(output, GLOSSARY_BM25)["glossary"]
        assert isinstance(record, dict)
        direct_matches = direct.glossary.matches
        assert record["expansions"] == list(direct_matches.expansions)
        assert [f["entry_ids"] for f in cast(list[dict[str, JsonValue]], record["fired"])] == [
            list(f.entry_ids) for f in direct_matches.fired
        ]


def test_the_output_says_how_many_entries_the_case_carried_and_that_the_digest_is_claimed() -> None:
    fused = policy((GLOSSARY_BM25, 3), (GROUPED_BM25, 3))
    payload = frozen(request("연결과 중앙값"))
    output = evaluate_retrieval(fused, payload)
    record = lane_record(output, GLOSSARY_BM25)["glossary"]
    assert isinstance(record, dict)
    assert record["entries_carried"] == len(carried(payload)) == 3
    assert record["digest_verified"] is False
    assert record["digest"] == glossary().digest and record["min_confidence"] == "MEDIUM"
    assert record["expansions"] == ["median"]
    assert record["abstained"] == [{"matched": "연결", "reason": "AMBIGUOUS"}]
    assert output["evaluator"] == "retrieval-lane-dispatch:1.0.0"
    # The lane after it carries no glossary record.
    assert "glossary" not in lane_record(output, GROUPED_BM25)


def test_another_lane_policy_does_not_write_glossary_keys() -> None:
    other = policy((GROUPED_BM25, 3), (PROTECTED_EXACT, 6))
    output = evaluate_retrieval(other, frozen(request("중앙값이 뭐야")))
    assert all("glossary" not in lane for lane in cast(list[dict[str, JsonValue]], output["lanes"]))
    assert not any("glossary" in key for key in cast(dict[str, JsonValue], output["lane"]))


def test_a_case_without_a_glossary_is_written_without_the_field() -> None:
    spans, catalog = report()
    plain = RetrievalRequest("v2 keys median", (), spans, (), catalog)
    assert "glossary" not in evaluation_case_payload(plain)
    assert "glossary" in evaluation_case_payload(request("중앙값"))


def test_the_evaluator_refuses_a_case_whose_digest_is_not_the_policys_or_that_has_none() -> None:
    fused = policy((GLOSSARY_BM25, 3), (GROUPED_BM25, 3))
    other = build_glossary([make_entry("중앙값", "average", "perf", "HIGH")])
    with pytest.raises(ValueError, match="RETRIEVAL_GLOSSARY_DIGEST_MISMATCH"):
        evaluate_retrieval(fused, frozen(request("중앙값", held=other)))
    spans, catalog = report()
    bare = RetrievalRequest("중앙값", (), spans, (), catalog)
    with pytest.raises(ValueError, match="RETRIEVAL_GLOSSARY_UNAVAILABLE"):
        evaluate_retrieval(fused, frozen(bare))


def test_the_evaluator_checks_each_entry_against_its_own_id() -> None:
    fused = policy((GLOSSARY_BM25, 3), (GROUPED_BM25, 3))
    payload = frozen(request("중앙값이 뭐야"))
    carried(payload)[0]["en"] = "average"
    with pytest.raises(ValidationError, match="RETRIEVAL_CASE_GLOSSARY_ENTRY_INVALID"):
        evaluate_retrieval(fused, payload)
    forged = frozen(request("중앙값이 뭐야"))
    carried(forged)[0]["entry_id"] = "0" * 16
    with pytest.raises(ValidationError, match="RETRIEVAL_CASE_GLOSSARY_ENTRY_INVALID"):
        evaluate_retrieval(fused, forged)


def test_the_evaluator_refuses_an_entry_carried_twice() -> None:
    fused = policy((GLOSSARY_BM25, 3), (GROUPED_BM25, 3))
    payload = frozen(request("중앙값이 뭐야"))
    entries = carried(payload)
    entries.append(dict(entries[0]))
    with pytest.raises(ValidationError, match="RETRIEVAL_CASE_GLOSSARY_ENTRY_DUPLICATED"):
        evaluate_retrieval(fused, payload)
