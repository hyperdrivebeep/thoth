"""G3 1c: ROUND_ROBIN_DEDUP 1.0.0 merges ranked chunks and admits them under a budget."""

from tests.unit.test_retrieval_selection_oracle import span

from thoth.domain.evidence import EvidenceSpan
from thoth.domain.retrieval_fusion import (
    FusedChunk,
    RankedLane,
    admit_chunks,
    lane_contributions,
    merge_round_robin,
)

TEXT = "x" * 10


def sp(name: str, size: int = 10) -> EvidenceSpan:
    return span(name, "x" * size, line=1)


def lane(name: str, depth: int, *chunks: tuple[EvidenceSpan, ...]) -> RankedLane:
    return RankedLane(name, "1.0.0", depth, chunks)


def ids(order: list[FusedChunk] | tuple[FusedChunk, ...]) -> list[tuple[int, list[str]]]:
    return [(c.lane_index, [s.span_id[-1] for s in c.spans]) for c in order]


def test_lanes_take_turns_rank_by_rank_in_the_order_the_policy_lists_them() -> None:
    a = lane("A", 9, (sp("a"),), (sp("b"),), (sp("c"),))
    b = lane("B", 9, (sp("d"),), (sp("e"),))
    assert ids(merge_round_robin([a, b])) == [
        (0, ["a"]),
        (1, ["d"]),
        (0, ["b"]),
        (1, ["e"]),
        (0, ["c"]),
    ]
    # The policy's order is the tie-break: swapping the lanes swaps who goes first.
    assert ids(merge_round_robin([b, a]))[:2] == [(0, ["d"]), (1, ["a"])]


def test_each_lane_is_cut_to_its_own_depth_before_the_merge() -> None:
    a = lane("A", 1, (sp("a"),), (sp("b"),), (sp("c"),))
    b = lane("B", 2, (sp("d"),), (sp("e"),), (sp("f"),))
    assert ids(merge_round_robin([a, b])) == [(0, ["a"]), (1, ["d"]), (1, ["e"])]


def test_a_lane_with_nothing_to_give_is_skipped_and_the_others_go_on() -> None:
    empty = lane("A", 5)
    full = lane("B", 5, (sp("a"),), (sp("b"),))
    assert ids(merge_round_robin([empty, full])) == [(1, ["a"]), (1, ["b"])]
    assert merge_round_robin([empty, empty]) == ()


def test_the_merge_is_deterministic_and_does_not_look_at_the_text() -> None:
    a = lane("A", 3, (sp("a", 3),), (sp("b", 90),))
    b = lane("B", 3, (sp("c", 50),), (sp("d", 1),))
    first = merge_round_robin([a, b])
    assert first == merge_round_robin([a, b])
    assert ids(first) == [(0, ["a"]), (1, ["c"]), (0, ["b"]), (1, ["d"])]


def test_a_span_given_twice_is_admitted_once_and_credited_to_the_first_lane() -> None:
    a, b = sp("a"), sp("b")
    one = lane("A", 3, (a,), (b,))
    two = lane("B", 3, (b,), (a,))
    selected: list[EvidenceSpan] = []
    credited = admit_chunks(merge_round_robin([one, two]), selected, 10, 1000)
    assert [s.span_id[-1] for s in selected] == ["a", "b"]
    # a is first from lane 0; b is rank 0 of lane 1, which comes before rank 1 of lane 0.
    assert credited == {"span:a": 0, "span:b": 1}


def test_pinned_spans_stay_first_and_are_not_credited_to_a_lane() -> None:
    pinned, other = sp("p"), sp("o")
    selected = [pinned]
    credited = admit_chunks(
        merge_round_robin([lane("A", 3, (pinned,), (other,))]), selected, 10, 1000
    )
    assert [s.span_id[-1] for s in selected] == ["p", "o"]
    assert credited == {"span:o": 0}


def test_the_span_and_character_budgets_apply_in_the_merged_order() -> None:
    chunks = [(sp(n),) for n in "abcd"]
    capped: list[EvidenceSpan] = []
    admit_chunks(merge_round_robin([lane("A", 9, *chunks)]), capped, 2, 1000)
    assert [s.span_id[-1] for s in capped] == ["a", "b"]
    small: list[EvidenceSpan] = []
    admit_chunks(merge_round_robin([lane("A", 9, *chunks)]), small, 10, 25)
    assert [s.span_id[-1] for s in small] == ["a", "b"]


def test_a_group_ends_at_its_first_span_that_does_not_fit_and_leaves_no_hole() -> None:
    title, small, big, after = sp("t", 5), sp("s", 5), sp("b", 50), sp("z", 5)
    group = (title, small, big, after)
    selected: list[EvidenceSpan] = []
    admit_chunks(merge_round_robin([lane("G", 1, group)]), selected, 10, 30)
    # "z" would fit the budget on its own, but it comes after the span that did not.
    assert [s.span_id[-1] for s in selected] == ["t", "s"]


def test_one_large_group_does_not_keep_out_the_small_chunk_that_follows_it() -> None:
    title, big = sp("t", 5), sp("b", 50)
    later = sp("l", 5)
    one = lane("G", 1, (title, big))
    two = lane("S", 1, (later,))
    selected: list[EvidenceSpan] = []
    admit_chunks(merge_round_robin([one, two]), selected, 10, 30)
    assert [s.span_id[-1] for s in selected] == ["t", "l"]


def test_a_span_already_admitted_is_skipped_inside_a_later_chunk_without_ending_it() -> None:
    shared, fresh = sp("a"), sp("b")
    selected: list[EvidenceSpan] = []
    admit_chunks(
        merge_round_robin(
            [lane("A", 1, (shared,)), lane("B", 1, (shared, fresh))],
        ),
        selected,
        10,
        1000,
    )
    assert [s.span_id[-1] for s in selected] == ["a", "b"]


def test_the_contribution_of_each_lane_shows_what_it_gave_and_what_it_also_found() -> None:
    a, b, c = sp("a"), sp("b"), sp("c")
    one = lane("A", 2, (a,), (b,))
    two = lane("B", 2, (b,), (c,), (sp("d"),))
    selected: list[EvidenceSpan] = []
    credited = admit_chunks(merge_round_robin([one, two]), selected, 10, 1000)
    first, second = lane_contributions([one, two], selected, credited)
    assert (first.lane_id, first.depth, first.ranked) == ("A", 2, 2)
    # Order: A:a, B:b, A:b (already given), B:c.
    assert first.credited_refs == ("span:a",) and first.also_refs == ("span:b",)
    assert (second.depth, second.ranked) == (2, 3)
    # d was beyond B's depth, so B did not give it.
    assert second.credited_refs == ("span:b", "span:c") and second.also_refs == ()
    assert "span:d" not in {*second.credited_refs, *second.also_refs}
