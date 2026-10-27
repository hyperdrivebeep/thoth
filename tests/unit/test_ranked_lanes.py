"""G3 1b: GROUPED_BM25 and CHAR_BIGRAM, ranked by BM25 under the same selection frame."""

import pytest
from pydantic import JsonValue
from tests.unit.group_fixtures import Reader, context, parse
from tests.unit.test_retrieval_selection_oracle import span
from tests.unit.test_retrieval_source_digests import MARKDOWN

from thoth.adapters.behavior.registry import evaluate_retrieval
from thoth.domain.behavior_policy import RetrievalBehaviorInput, RetrievalLanePolicy
from thoth.domain.enums import CutoffState
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.lane_scoring import BIGRAM_VERSION, BM25_VERSION
from thoth.domain.query_identifiers import SCANNER_VERSION
from thoth.domain.retrieval_lane import (
    CHAR_BIGRAM,
    GROUPED_BM25,
    LANE_PROVENANCE,
    LANE_VERSIONS,
    LANES,
    LEXICAL_SUBSTRING_V1,
    CatalogContext,
    RetrievalRequest,
    select_retrieval_context,
)
from thoth.domain.structure_groups import GROUPING_VERSION, CatalogGroup

FILLER = " with enough words around it to be an ordinary line"
# The 24-span report is small enough to be returned whole; a span limit below it makes the
# frame hand the choice to the lane.
SIX = 6


def policy(lane: str, **budget: int) -> RetrievalLanePolicy:
    return RetrievalLanePolicy.model_validate({**budget, "lanes": [{"lane_id": lane}]})


def report() -> tuple[tuple[EvidenceSpan, ...], CatalogContext]:
    reader = Reader()
    spans = parse(MARKDOWN, "rep", reader)
    return spans, context(spans, reader).to_catalog()


def chosen(request: RetrievalRequest, lane: RetrievalLanePolicy) -> list[int]:
    return [int(s.span_id[-4:]) for s in select_retrieval_context(request, lane).selected]


def test_the_new_lanes_are_registered_with_a_version_and_declare_their_rules() -> None:
    assert {GROUPED_BM25, CHAR_BIGRAM} <= set(LANES)
    assert set(LANES) == set(LANE_VERSIONS)
    assert LANE_VERSIONS[GROUPED_BM25] == LANE_VERSIONS[CHAR_BIGRAM] == "1.0.0"
    assert dict(LANE_PROVENANCE[GROUPED_BM25]) == {
        "grouping": GROUPING_VERSION,
        "scoring": BM25_VERSION,
        "scanner": SCANNER_VERSION,
    }
    assert dict(LANE_PROVENANCE[CHAR_BIGRAM]) == {"scoring": BM25_VERSION, "terms": BIGRAM_VERSION}


def test_each_new_lane_is_a_policy_of_its_own_and_two_lanes_are_not_allowed() -> None:
    lanes = {
        policy(lane).model_dump_json() for lane in (LEXICAL_SUBSTRING_V1, GROUPED_BM25, CHAR_BIGRAM)
    }
    assert len(lanes) == 3
    with pytest.raises(ValueError, match="RETRIEVAL_POLICY_ONE_LANE_SUPPORTED"):
        RetrievalLanePolicy.model_validate(
            {"lanes": [{"lane_id": GROUPED_BM25}, {"lane_id": CHAR_BIGRAM}]}
        )


def test_a_row_of_numbers_is_found_through_the_version_title_above_it() -> None:
    spans, catalog = report()
    request = RetrievalRequest("v2 keys median", (), spans, (), catalog)
    # The v2 table: its title lines first, then its body in document order.
    assert chosen(request, policy(GROUPED_BM25, max_spans=SIX)) == [2, 4, 10, 11, 12, 13]
    # The numeric rows have no term of their own, so the original lane never reaches them.
    original = chosen(request, policy(LEXICAL_SUBSTRING_V1, max_spans=SIX))
    assert 12 not in original and 13 not in original


def test_a_budget_that_ends_inside_a_group_keeps_its_titles() -> None:
    spans, catalog = report()
    request = RetrievalRequest("v2 keys median", (), spans, (), catalog)
    assert chosen(request, policy(GROUPED_BM25, max_spans=3)) == [2, 4, 10]
    tight = policy(GROUPED_BM25, max_spans=SIX, character_budget=60)
    picked = select_retrieval_context(request, tight).selected
    assert sum(len(s.exact_text) for s in picked) <= 60
    assert picked[0].span_id.endswith("0002")


def test_a_span_given_once_is_not_given_again_from_another_group() -> None:
    spans, catalog = report()
    request = RetrievalRequest("keys median performance version", (), spans, (), catalog)
    picked = chosen(request, policy(GROUPED_BM25, max_spans=12))
    assert len(picked) == len(set(picked)) > SIX


def test_without_groups_each_span_is_its_own_unit() -> None:
    spans, catalog = report()
    flat = CatalogContext(catalog.ordered_refs, catalog.protected_refs, True, 0)
    request = RetrievalRequest("v2 keys median", (), spans, (), flat)
    picked = chosen(request, policy(GROUPED_BM25, max_spans=SIX))
    assert 12 not in picked and 13 not in picked and 10 in picked
    assert (
        chosen(RetrievalRequest("v2 keys median", (), spans), policy(GROUPED_BM25, max_spans=SIX))
        == picked
    )


def test_spans_that_no_group_names_are_scored_alone() -> None:
    a = span("a", "alpha reading of the first test" + FILLER, line=1)
    b = span("b", "alpha reading of the second test" + FILLER, line=2)
    c = span("c", "unrelated closing text" + FILLER, line=3)
    catalog = CatalogContext(
        ("span:a", "span:b", "span:c"),
        frozenset(),
        True,
        0,
        (CatalogGroup("SECTION", (), ("span:a",)),),
    )
    request = RetrievalRequest("alpha test", (), (a, b, c), (), catalog)
    picked = select_retrieval_context(request, policy(GROUPED_BM25, max_spans=2)).selected
    assert [s.span_id for s in picked] == ["span:a", "span:b"]


def test_the_frequencies_are_those_of_the_spans_the_request_may_read() -> None:
    a = span("a", "needle in the first line of text" + FILLER, line=1)
    b = span("b", "common words in the second line" + FILLER, line=2)
    hidden = span(
        "h",
        "needle needle needle hidden text" + FILLER,
        line=3,
        cutoff=CutoffState.AFTER_CUTOFF,
    )
    lane = policy(GROUPED_BM25, max_spans=1)
    plain = select_retrieval_context(RetrievalRequest("needle", (), (a, b)), lane)
    with_hidden = select_retrieval_context(RetrievalRequest("needle", (), (a, b, hidden)), lane)
    assert [s.span_id for s in plain.selected] == ["span:a"]
    assert [s.span_id for s in with_hidden.selected] == ["span:a"]
    assert hidden.span_id not in [s.span_id for s in with_hidden.eligible]


def test_pinned_spans_are_left_to_the_frame_and_not_ranked_twice() -> None:
    spans, catalog = report()
    request = RetrievalRequest("v2 keys median", (), spans, (spans[12].span_id,), catalog)
    picked = chosen(request, policy(GROUPED_BM25, max_spans=SIX))
    assert picked[0] == 12 and picked.count(12) == 1


def test_equal_scores_keep_the_document_order_of_the_units() -> None:
    one = span("a", "shared token here" + FILLER, line=1)
    two = span("b", "shared token here" + FILLER, line=2)
    lane = policy(GROUPED_BM25, max_spans=1)
    first = select_retrieval_context(RetrievalRequest("shared token", (), (two, one)), lane)
    assert [s.span_id for s in first.selected] == ["span:a"]


def test_the_selection_says_which_lane_version_and_rules_ranked_it() -> None:
    spans, catalog = report()
    request = RetrievalRequest("v2 keys median", (), spans, (), catalog)
    grouped = dict(
        select_retrieval_context(request, policy(GROUPED_BM25, max_spans=SIX)).provenance
    )
    assert grouped["lane_id"] == GROUPED_BM25 and grouped["lane_version"] == "1.0.0"
    assert grouped["grouping"] == GROUPING_VERSION and grouped["scoring"] == BM25_VERSION
    bigram = dict(select_retrieval_context(request, policy(CHAR_BIGRAM, max_spans=SIX)).provenance)
    assert bigram["lane_id"] == CHAR_BIGRAM and bigram["terms"] == BIGRAM_VERSION
    original = select_retrieval_context(request, policy(LEXICAL_SUBSTRING_V1, max_spans=SIX))
    assert original.provenance == ()


def test_a_small_catalog_is_returned_whole_by_the_frame_and_the_lane_is_not_used() -> None:
    spans, catalog = report()
    request = RetrievalRequest("v2 keys median", (), spans, (), catalog)
    for lane in (GROUPED_BM25, CHAR_BIGRAM):
        selection = select_retrieval_context(request, policy(lane))
        assert selection.mode == "FULL_ELIGIBLE_EXTRACTED_CATALOG" and selection.provenance == ()
        assert [s.span_id for s in selection.selected] == list(catalog.ordered_refs)


def test_the_evaluation_reads_groups_only_when_the_case_names_them() -> None:
    spans, catalog = report()
    case_catalog: dict[str, JsonValue] = {
        "ordered_refs": list(catalog.ordered_refs),
        "protected_refs": [],
        "all_observed": True,
        "structure_count": 0,
    }
    without: dict[str, JsonValue] = {
        "problem": "v2 keys median",
        "evidence": [s.model_dump(mode="json") for s in spans],
        "catalog": case_catalog,
    }
    lane = policy(GROUPED_BM25, max_spans=SIX)
    plain = evaluate_retrieval(lane, without)
    assert "span:rep:0012" not in str(plain["selected_refs"]) and "lane" in plain
    groups: list[JsonValue] = [
        {"kind": g.kind, "title_refs": list(g.title_refs), "body_refs": list(g.body_refs)}
        for g in catalog.groups
    ]
    with_groups: dict[str, JsonValue] = {
        **without,
        "catalog": {**case_catalog, "groups": groups},
    }
    grouped = evaluate_retrieval(lane, with_groups)
    assert grouped["selected_refs"] == [f"span:rep:{i:04d}" for i in (2, 4, 10, 11, 12, 13)]
    dumped = RetrievalBehaviorInput.model_validate(with_groups).model_dump(mode="json")
    assert dumped["catalog"] is not None and dumped["catalog"]["groups"][0]["kind"] == "SECTION"


def test_char_bigram_finds_korean_text_through_syllable_pairs_without_a_shared_stem() -> None:
    a = span("a", "이 보고서에는 측정 결과가 적혀 있다" + FILLER, line=1)
    b = span("b", "여기에서 확인한 값은 아래와 같다" + FILLER, line=2)
    c = span("c", "전혀 관계없는 마지막 문장이다" + FILLER, line=3)
    request = RetrievalRequest("보고서를 확인", (), (a, b, c))
    picked = select_retrieval_context(request, policy(CHAR_BIGRAM, max_spans=2)).selected
    assert [s.span_id for s in picked] == ["span:a", "span:b"]
    assert "span:c" not in [s.span_id for s in picked]


def test_char_bigram_ignores_function_words_and_keeps_symbols_whole() -> None:
    a = span("a", "에서 에서 에서 only function words in this line" + FILLER, line=1)
    b = span("b", "the fill_nan call is slow" + FILLER, line=2)
    c = span("c", "fill and nan are separate words here" + FILLER, line=3)
    request = RetrievalRequest("에서 fill_nan", (), (a, b, c))
    picked = select_retrieval_context(request, policy(CHAR_BIGRAM, max_spans=2)).selected
    assert [s.span_id for s in picked] == ["span:b"]
