"""G3 1b: structure groups follow their rules and hold their invariants."""

import pytest
from tests.unit.group_fixtures import Reader, context, parse
from tests.unit.test_retrieval_source_digests import MARKDOWN

from thoth.domain import structure_groups
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.structure_groups import CatalogGroup, derive_block_groups


def numbers(
    groups: tuple[CatalogGroup, ...], spans: tuple[EvidenceSpan, ...]
) -> list[tuple[str, list[int], list[int]]]:
    index = {s.span_id: i for i, s in enumerate(spans)}
    return [
        (g.kind, [index[r] for r in g.title_refs], [index[r] for r in g.body_refs]) for g in groups
    ]


def groups_of(markdown: str) -> tuple[tuple[CatalogGroup, ...], tuple[EvidenceSpan, ...]]:
    reader = Reader()
    spans = parse(markdown, "doc", reader)
    return context(spans, reader).groups, spans


def test_a_report_is_cut_into_sections_fences_with_their_titles_and_lists() -> None:
    groups, spans = groups_of(MARKDOWN)
    assert numbers(groups, spans) == [
        ("SECTION", [0], [0, 1]),
        ("SECTION", [0, 2], [2, 3, 4]),
        ("FENCE", [2, 4], [5]),
        # A separator line starts a sub-group and is part of its title and its body.
        ("FENCE", [2, 4, 6], [6, 7, 8, 9]),
        ("FENCE", [2, 4, 10], [10, 11, 12, 13, 14]),
        ("SECTION", [0, 15], [15]),
        # A top-level item with a nested list is a group of its own.
        ("LIST", [15], [16, 17, 18]),
        ("LIST", [15], [19, 20, 21]),
        ("SECTION", [0, 22], [22, 23]),
    ]


def test_every_span_is_in_exactly_one_body_and_titles_may_repeat() -> None:
    groups, spans = groups_of(MARKDOWN)
    bodies = [r for g in groups for r in g.body_refs]
    assert sorted(bodies) == sorted(s.span_id for s in spans)
    titles = [r for g in groups for r in g.title_refs]
    assert len(titles) > len(set(titles))


def test_a_group_never_holds_spans_of_two_sources() -> None:
    reader = Reader()
    first = parse(MARKDOWN, "one", reader)
    second = parse(MARKDOWN.replace("v2", "v9"), "two", reader)
    pooled = (*first, *second)
    groups = context(pooled, reader).groups
    source = {s.span_id: s.source_version_id for s in pooled}
    assert sorted(r for g in groups for r in g.body_refs) == sorted(source)
    for group in groups:
        assert len({source[r] for r in (*group.title_refs, *group.body_refs)}) == 1


def test_a_group_cut_for_size_keeps_the_title_of_the_group_it_was_cut_from(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(structure_groups, "MAX_GROUP_SPANS", 2)
    groups, spans = groups_of(MARKDOWN)
    cut = [g for g in numbers(groups, spans) if g[0] == "FENCE" and g[1] == [2, 4, 6]]
    assert [g[2] for g in cut] == [[6, 7], [8, 9]]
    assert sorted(r for g in groups for r in g.body_refs) == sorted(s.span_id for s in spans)


def test_a_group_cut_for_characters_keeps_its_title_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(structure_groups, "MAX_GROUP_CHARS", 30)
    groups, spans = groups_of(MARKDOWN)
    cut = [g for g in numbers(groups, spans) if g[0] == "FENCE" and g[1] == [2, 4, 10]]
    assert len(cut) > 1 and all(g[1] == [2, 4, 10] for g in cut)


def test_a_code_comment_that_looks_like_a_separator_splits_a_fence_and_the_rule_is_unchanged() -> (
    None
):
    source = "### Code\n\n```python\nx = 1\n### a comment\ny = 2\n```\n"
    groups, spans = groups_of(source)
    fence_groups = [g for g in numbers(groups, spans) if g[0] == "FENCE"]
    texts = {i: s.exact_text for i, s in enumerate(spans)}
    assert len(fence_groups) == 2
    assert texts[fence_groups[1][2][0]] == "### a comment"
    assert fence_groups[1][2][0] in fence_groups[1][1]


def test_flat_items_are_one_group_and_an_item_with_a_nested_list_is_another() -> None:
    source = "## Items\n\n* one\n* two\n* parent\n  * child a\n  * child b\n"
    groups, spans = groups_of(source)
    texts = [[spans[i].exact_text for i in g[2]] for g in numbers(groups, spans) if g[0] == "LIST"]
    # Groups come in document order of their first line.
    assert texts == [["* one", "* two"], ["* parent", "* child a", "* child b"]]


def test_text_before_the_first_heading_is_an_untitled_section() -> None:
    groups, spans = groups_of("A line before any heading.\n\n## Later\n\nBody text.\n")
    first = numbers(groups, spans)[0]
    assert first[0] == "SECTION" and first[1] == [] and first[2] == [0]


def test_spans_no_region_claims_still_land_in_exactly_one_group() -> None:
    groups, spans = groups_of(
        "Setext heading\n==============\n\nBody under it.\n\n## Real\n\nText.\n"
    )
    assert sorted(r for g in groups for r in g.body_refs) == sorted(s.span_id for s in spans)


def test_a_structured_source_is_grouped_by_its_parent_pointer() -> None:
    texts = {"a": "x", "b": "y", "c": "z", "d": "w"}
    groups = derive_block_groups(
        [("a", "/rows/0/metric"), ("b", "/rows/0/value"), ("c", "/rows/1/metric"), ("d", None)],
        texts,
    )
    assert [(g.kind, g.title_refs, g.body_refs) for g in groups] == [("BLOCK", (), ("a", "b"))]


def test_the_groups_do_not_enter_the_source_basis() -> None:
    reader = Reader()
    spans = parse(MARKDOWN, "doc", reader)
    with_groups = context(spans, reader)
    assert with_groups.groups
    without = with_groups.__class__(
        with_groups.ordered_refs,
        with_groups.protected_refs,
        with_groups.all_observed,
        with_groups.structure_count,
        with_groups.basis_digest,
        with_groups.limitations,
    )
    assert without.groups == () and without.basis_digest == with_groups.basis_digest
