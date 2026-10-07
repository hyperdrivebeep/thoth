"""Pins what the Docling location helpers return today, so that moving them changes nothing.

These helpers turn the character spans and boxes of a Docling export into THOTH locators. The
expected values below were taken from the code before it was split into its own module.
"""

from __future__ import annotations

from typing import Any

import pytest
from tests.unit.test_parser_capability_selection import artifact_for

from thoth.adapters.parsers.docling_locations import (
    _charspan,
    _located_segments,
    _segment_node_id,
    _source_locator,
    _text_coordinate_basis,
    _validated_charspan,
)

MISSING = "Docling multi-provenance item omitted a charspan"
INVALID = "Docling multi-provenance item reported an invalid charspan"
OVERLAP = "Docling multi-provenance item reported overlapping charspans"
UNLOCATED = "Docling multi-provenance item has unlocated non-whitespace text"
TRAILING = "Docling multi-provenance item has trailing unlocated text"
MISMATCH = "Docling text and orig differ; charspan coordinate basis is unverified"
OUTSIDE = "Docling provenance charspan is outside the item text"


def test_segment_node_ids_are_stable_and_depend_on_every_part_of_the_segment() -> None:
    artifact = artifact_for(b"docling-locations-characterization")
    first = _segment_node_id(artifact, "#/texts/0", 0, 0, 5)
    assert first == "node:6c1b7427dcc6966c8061c314"
    assert _segment_node_id(artifact, "#/texts/0", 1, 6, 11) == "node:adbdfb63da3b403fe1eccf15"
    assert _segment_node_id(artifact, "#/texts/0", 0, 0, 5) == first
    other = artifact.model_copy(update={"artifact_id": "another-artifact"})
    variants = {
        _segment_node_id(other, "#/texts/0", 0, 0, 5),
        _segment_node_id(artifact, "#/texts/1", 0, 0, 5),
        _segment_node_id(artifact, "#/texts/0", 1, 0, 5),
        _segment_node_id(artifact, "#/texts/0", 0, 1, 5),
        _segment_node_id(artifact, "#/texts/0", 0, 0, 6),
    }
    assert first not in variants and len(variants) == 5
    assert first.startswith("node:") and len(first) == len("node:") + 24


@pytest.mark.parametrize(
    ("provenance", "expected"),
    [
        ({"charspan": [2, 9]}, (2, 9)),
        ({"charspan": (2, 9)}, (2, 9)),
        ({"charspan": {"start": 2, "end": 9}}, (2, 9)),
        ({"charspan": {"char_start": 2, "char_end": 9}}, (2, 9)),
        ({"char_span": [2, 9]}, (2, 9)),
        ({"char_start": 2, "char_end": 9}, (2, 9)),
        ({"charspan": [2, 9], "char_span": [4, 5]}, (2, 9)),
        ({"charspan": [2, 9, 11]}, None),
        ({"charspan": [2]}, None),
        ({"charspan": ["2", "9"]}, None),
        ({"charspan": [2, 9.0]}, None),
        ({"charspan": {"start": 2}}, None),
        ({"charspan": "2-9"}, None),
        ({"char_start": 2}, None),
        ({}, None),
    ],
)
def test_charspan_reads_each_form_and_refuses_anything_else(
    provenance: dict[str, Any], expected: tuple[int, int] | None
) -> None:
    assert _charspan(provenance) == expected


def _located(text: str, *spans: tuple[int, int]) -> Any:
    return _located_segments(
        text, [{"page_no": n + 1, "charspan": list(s)} for n, s in enumerate(spans)]
    )


def test_located_segments_are_sorted_with_the_end_exclusive_and_keep_their_provenance() -> None:
    text = "alpha beta"
    segments, problem = _located(text, (6, 10), (0, 5))
    assert problem is None and segments is not None
    assert [(start, end) for start, end, _ in segments] == [(0, 5), (6, 10)]
    # each span keeps its own provenance: the span given first is page 1 and sorts second
    assert [observed["page_no"] for _, _, observed in segments] == [2, 1]
    assert text[segments[0][0] : segments[0][1]] == "alpha"
    touching, problem = _located("alphabeta", (0, 5), (5, 9))
    assert problem is None and touching is not None and len(touching) == 2


@pytest.mark.parametrize(
    ("text", "spans", "message"),
    [
        ("alpha beta", [(0, 5), (4, 10)], OVERLAP),
        ("alpha beta", [(-1, 5), (6, 10)], INVALID),
        ("alpha beta", [(5, 5), (6, 10)], INVALID),
        ("alpha beta", [(0, 5), (6, 11)], INVALID),
        ("alpha XX beta", [(0, 5), (9, 13)], UNLOCATED),
        ("alpha beta XX", [(0, 5), (6, 10)], TRAILING),
        ("XX alpha", [(3, 8)], UNLOCATED),
    ],
)
def test_located_segments_refuse_gaps_overlaps_and_ranges_outside_the_text(
    text: str, spans: list[tuple[int, int]], message: str
) -> None:
    segments, problem = _located(text, *spans)
    assert segments is None and problem == message


def test_located_segments_name_a_missing_charspan_and_allow_whitespace_gaps() -> None:
    assert _located_segments("alpha beta", [{"page_no": 1}]) == (None, MISSING)
    segments, problem = _located("alpha   beta  ", (0, 5), (8, 12))
    assert problem is None and segments is not None and len(segments) == 2


def test_the_coordinate_basis_is_the_orig_text_unless_it_differs_from_text() -> None:
    assert _text_coordinate_basis({"text": "same", "orig": "same"}) == ("same", None)
    assert _text_coordinate_basis({"text": "only text"}) == ("only text", None)
    assert _text_coordinate_basis({"orig": "only orig"}) == ("only orig", None)
    assert _text_coordinate_basis({"text": "a", "orig": "b"}) == ("", MISMATCH)
    assert _text_coordinate_basis({}) == ("", None)
    assert _text_coordinate_basis({"text": 3, "orig": None}) == ("", None)


def test_a_validated_charspan_is_inside_the_text_with_the_end_exclusive() -> None:
    item = {"text": "exact text", "orig": "exact text"}
    assert _validated_charspan(item, {"charspan": [0, 10]}) == ((0, 10), None)
    assert _validated_charspan(item, {"charspan": [6, 10]}) == ((6, 10), None)
    assert _validated_charspan(item, {"charspan": [0, 11]}) == (None, OUTSIDE)
    assert _validated_charspan(item, {"charspan": [-1, 4]}) == (None, OUTSIDE)
    assert _validated_charspan(item, {"charspan": [4, 4]}) == (None, OUTSIDE)
    assert _validated_charspan(item, {"charspan": [5, 4]}) == (None, OUTSIDE)


def test_a_missing_span_is_not_reported_and_a_text_mismatch_is_reported_only_with_a_span() -> None:
    assert _validated_charspan({"text": "a"}, {}) == (None, None)
    assert _validated_charspan({"text": "a", "orig": "b"}, {}) == (None, None)
    assert _validated_charspan({"text": "a", "orig": "b"}, {"charspan": [0, 1]}) == (None, MISMATCH)


EXPORT = {"pages": {"1": {"size": {"height": 100}}, "2": {"size": {}}}}


def _locator(prov: list[dict[str, Any]], **kwargs: Any) -> Any:
    return _source_locator(EXPORT, {"prov": prov}, "#/texts/0", **kwargs)


def test_a_locator_without_exactly_one_provenance_keeps_only_the_pointer() -> None:
    for prov in ([], [{"page_no": 1}, {"page_no": 2}]):
        locator = _locator(prov)
        assert locator.json_pointer == "#/texts/0"
        assert locator.page is None and locator.bbox is None and locator.char_start is None


def test_a_top_left_box_is_kept_as_left_top_right_bottom_with_the_page_number() -> None:
    box = {"l": 10, "t": 20, "r": 110, "b": 40, "coord_origin": "TOPLEFT"}
    locator = _locator([{"page_no": 1, "bbox": box}])
    assert locator.page == 1 and locator.bbox == (10, 20, 110, 40)
    assert locator.char_start is None and locator.char_end is None


def test_a_bottom_left_box_is_flipped_with_the_page_height_and_dropped_without_one() -> None:
    box = {"l": 10, "t": 80, "r": 110, "b": 60, "coord_origin": "BOTTOMLEFT"}
    assert _locator([{"page_no": 1, "bbox": box}]).bbox == (10, 20, 110, 40)
    without_height = _locator([{"page_no": 2, "bbox": box}])
    assert without_height.page == 2 and without_height.bbox is None
    assert _locator([{"page_no": 1}]).bbox is None


def test_one_given_provenance_and_a_char_range_become_the_locator() -> None:
    box = {"l": 1, "t": 2, "r": 3, "b": 4, "coord_origin": "TOPLEFT"}
    given = {"page_no": 2, "bbox": box}
    locator = _source_locator(
        EXPORT,
        {"prov": [{"page_no": 1}, {"page_no": 5}]},
        "#/texts/9",
        provenance=given,
        char_range=(3, 8),
    )
    assert (locator.page, locator.bbox) == (2, (1, 2, 3, 4))
    assert (locator.char_start, locator.char_end, locator.json_pointer) == (3, 8, "#/texts/9")
