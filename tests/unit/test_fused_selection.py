"""G3 1c: retrieval policy 4.0.0 selects through the shared frame by merging its lanes."""

import pytest
from tests.unit.test_ranked_lanes import report
from tests.unit.test_retrieval_selection_oracle import span

from thoth.domain.behavior_policy import RetrievalFusionPolicy
from thoth.domain.retrieval_lane import (
    CHAR_BIGRAM,
    GROUPED_BM25,
    LANE_VERSIONS,
    LANES,
    LEXICAL_SUBSTRING_V1,
    PROTECTED_EXACT,
    CatalogContext,
    RetrievalRequest,
    select_retrieval_context,
)
from thoth.domain.structure_groups import CatalogGroup

PAD = " padding words so this line is an ordinary one"


def fused(*lanes: tuple[str, int], **budget: int) -> RetrievalFusionPolicy:
    return RetrievalFusionPolicy.model_validate(
        {**budget, "lanes": [{"lane_id": lane, "depth": depth} for lane, depth in lanes]}
    )


def numbers(request: RetrievalRequest, policy: RetrievalFusionPolicy) -> list[int]:
    return [int(s.span_id[-4:]) for s in select_retrieval_context(request, policy).selected]


def test_two_lanes_take_turns_and_the_policy_order_decides_who_goes_first() -> None:
    spans, catalog = report()
    request = RetrievalRequest("v2 keys median", (), spans, (), catalog)
    grouped_first = fused((GROUPED_BM25, 1), (PROTECTED_EXACT, 3), max_spans=12)
    exact_first = fused((PROTECTED_EXACT, 3), (GROUPED_BM25, 1), max_spans=12)
    assert numbers(request, grouped_first) == [2, 4, 10, 11, 12, 13, 14, 16, 23]
    assert numbers(request, exact_first) == [10, 2, 4, 11, 12, 13, 14, 16, 23]


def test_depth_is_counted_in_chunks_and_a_group_is_one_chunk() -> None:
    spans, catalog = report()
    request = RetrievalRequest("v2 keys median", (), spans, (), catalog)
    # One group (seven spans) from GROUPED_BM25 and three single spans from PROTECTED_EXACT.
    selection = select_retrieval_context(
        request, fused((GROUPED_BM25, 1), (PROTECTED_EXACT, 3), max_spans=12)
    )
    grouped, exact = selection.contributions
    assert (grouped.depth, grouped.ranked) == (1, 4) and len(grouped.credited_refs) == 7
    assert (exact.depth, exact.ranked) == (3, 6)
    assert exact.credited_refs == ("span:rep:0016", "span:rep:0023")
    assert exact.also_refs == ("span:rep:0010",)


def test_a_third_lane_adds_a_span_only_it_ranked_high() -> None:
    spans, catalog = report()
    request = RetrievalRequest("v2 keys median", (), spans, (), catalog)
    three = fused((GROUPED_BM25, 2), (PROTECTED_EXACT, 4), (CHAR_BIGRAM, 4), max_spans=12)
    assert numbers(request, three) == [2, 4, 10, 11, 12, 13, 14, 7, 6, 8, 9, 16]
    selection = select_retrieval_context(request, three)
    # Span 7 is the first-ranked span of CHAR_BIGRAM; the other two lanes reach it only deeper.
    assert selection.contributions[2].credited_refs == ("span:rep:0007",)


def test_the_selection_records_its_method_lanes_depths_and_rule_versions() -> None:
    spans, catalog = report()
    request = RetrievalRequest("v2 keys median", (), spans, (), catalog)
    selection = select_retrieval_context(
        request, fused((GROUPED_BM25, 2), (PROTECTED_EXACT, 4), max_spans=12)
    )
    assert dict(selection.provenance) == {
        "fusion": "ROUND_ROBIN_DEDUP",
        "fusion_version": "1.0.0",
        "lane0": "GROUPED_BM25:1.0.0:depth=2",
        "lane0.grouping": "structure-groups:1.0.0",
        "lane0.scoring": "bm25:k1=1.2;b=0.75;idf=ln(1+(N-n+0.5)/(n+0.5))",
        "lane0.scanner": "query-identifiers:1.0.0",
        "lane1": "PROTECTED_EXACT:1.0.0:depth=4",
        "lane1.scanner": "query-identifiers:1.0.0",
    }
    assert [p[0] for p in selection.provenance] == list(dict(selection.provenance))


def test_a_catalog_that_fits_is_returned_whole_and_no_lane_runs() -> None:
    spans, catalog = report()
    request = RetrievalRequest("v2 keys median", (), spans, (), catalog)
    selection = select_retrieval_context(request, fused((GROUPED_BM25, 1), (PROTECTED_EXACT, 3)))
    assert selection.mode == "FULL_ELIGIBLE_EXTRACTED_CATALOG"
    assert [s.span_id for s in selection.selected] == list(catalog.ordered_refs)
    assert selection.provenance == () and selection.contributions == ()


def test_a_pinned_span_comes_first_and_is_not_ranked_again() -> None:
    spans, catalog = report()
    pin = spans[20].span_id
    request = RetrievalRequest("v2 keys median", (), spans, (pin,), catalog)
    selection = select_retrieval_context(
        request, fused((GROUPED_BM25, 1), (PROTECTED_EXACT, 3), max_spans=12)
    )
    refs = [s.span_id for s in selection.selected]
    assert refs[0] == pin and refs.count(pin) == 1
    credited = {ref for c in selection.contributions for ref in c.credited_refs}
    assert pin not in credited


def test_an_unregistered_lane_or_lane_version_refuses_the_whole_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spans, catalog = report()
    request = RetrievalRequest("v2 keys median", (), spans, (), catalog)
    policy = fused((GROUPED_BM25, 1), (PROTECTED_EXACT, 3), max_spans=12)
    monkeypatch.setitem(LANE_VERSIONS, PROTECTED_EXACT, "1.1.0")
    with pytest.raises(ValueError, match="RETRIEVAL_LANE_VERSION_UNKNOWN"):
        select_retrieval_context(request, policy)
    monkeypatch.undo()
    monkeypatch.delitem(LANES, GROUPED_BM25)
    with pytest.raises(ValueError, match="RETRIEVAL_LANE_UNKNOWN"):
        select_retrieval_context(request, policy)


def test_a_fused_policy_does_not_take_a_lane_id_from_the_caller() -> None:
    spans, catalog = report()
    request = RetrievalRequest("v2 keys median", (), spans, (), catalog)
    with pytest.raises(ValueError, match="RETRIEVAL_LANE_ID_NOT_ALLOWED_FOR_FUSION"):
        select_retrieval_context(
            request, fused((GROUPED_BM25, 1), (PROTECTED_EXACT, 3)), LEXICAL_SUBSTRING_V1
        )


# A group of a title and three rows, one of them very long, and a separate span.
TITLE = span("t", "table heading for the sort run" + PAD, line=1)
ROW1 = span("r1", "alpha row one" + PAD, line=2)
BIG = span("r2", "alpha row two " + "w" * 200, line=3)
ROW3 = span("r3", "alpha row three" + PAD, line=4)
ZETA = span("z", "separate zeta marker" + PAD, line=9)
GROUP_SPANS = (TITLE, ROW1, BIG, ROW3, ZETA)
GROUP_CATALOG = CatalogContext(
    tuple(s.span_id for s in GROUP_SPANS),
    frozenset(),
    True,
    0,
    (CatalogGroup("SECTION", ("span:t",), ("span:r1", "span:r2", "span:r3")),),
)


def group_case(characters: int, exact_depth: int = 4) -> list[str]:
    request = RetrievalRequest("alpha zeta", (), GROUP_SPANS, (), GROUP_CATALOG)
    policy = fused(
        (GROUPED_BM25, 1),
        (PROTECTED_EXACT, exact_depth),
        max_spans=9,
        character_budget=characters,
    )
    return [
        s.span_id.removeprefix("span:") for s in select_retrieval_context(request, policy).selected
    ]


def test_a_budget_that_ends_inside_a_group_keeps_its_title_and_leaves_no_hole() -> None:
    # Title and first row fit; the long row does not, and the last row after it is left out of
    # the group even though it would fit on its own.
    assert group_case(190) == ["t", "r1"]
    # 205 characters would also hold the last row (196 in all), yet the group ended at the long
    # row, and PROTECTED_EXACT at depth 2 does not reach that row.
    assert group_case(205, exact_depth=2) == ["t", "r1"]
    # With room for the long row the group is admitted up to the row after it.
    assert group_case(400) == ["t", "r1", "r2"]


def test_a_group_that_ends_early_does_not_keep_out_the_chunks_that_come_after_it() -> None:
    # The group stops at the long row, but the small row that PROTECTED_EXACT ranks as its own
    # chunk is still tried and admitted; the span after it no longer fits.
    assert group_case(260) == ["t", "r1", "r3"]
