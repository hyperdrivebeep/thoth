"""A CSV file is read by row: one header node and one node per data row, as "column=value" pairs."""

from __future__ import annotations

from tests.unit.test_research_structured_block_context import _artifact, _spans

from thoth.adapters.parsers.csv_parser import CsvParser
from thoth.application.services.research_retrieval import lexical_candidates, text_neighbors
from thoth.domain.artifact import StructuralDocument
from thoth.domain.enums import StructuralNodeKind as Kind


def parse(raw: bytes) -> StructuralDocument:
    return CsvParser().parse(_artifact(raw, "trials.csv", "artifact:csv"), raw)


def rows(document: StructuralDocument) -> list[tuple[str, str, str | None, str | None, int | None]]:
    return [
        (
            node.kind.value,
            node.text or "",
            node.locator.cell_range,
            node.locator.json_pointer,
            node.locator.line,
        )
        for node in document.nodes
    ]


def test_the_header_is_one_node_and_each_data_row_is_one_node_with_its_column_names() -> None:
    document = parse(
        b"row_kind,row_id,detected\nTARGET_TRIAL,SYN-T-RAIN-03,0\nTARGET_TRIAL,SYN-T-RAIN-04,1\n"
    )
    assert document.artifact.parser_version == "1.1.0"
    assert rows(document) == [
        ("HEADER", "row_kind, row_id, detected", "R1C1:R1C3", "/header", 1),
        (
            "ROW",
            "row_kind=TARGET_TRIAL, row_id=SYN-T-RAIN-03, detected=0",
            "R2C1:R2C3",
            "/rows/2",
            2,
        ),
        (
            "ROW",
            "row_kind=TARGET_TRIAL, row_id=SYN-T-RAIN-04, detected=1",
            "R3C1:R3C3",
            "/rows/3",
            3,
        ),
    ]


def test_empty_values_are_left_out_and_a_blank_row_keeps_the_numbering_of_the_file() -> None:
    document = parse(b"id,window,detected\nA-1,,1\n\n,W-2,\n,,\n")
    assert [(k, t, p) for k, t, _, p, _ in rows(document)] == [
        ("HEADER", "id, window, detected", "/header"),
        ("ROW", "id=A-1, detected=1", "/rows/2"),
        ("ROW", "window=W-2", "/rows/4"),
    ]


def test_quoted_values_with_commas_quotes_and_line_breaks_stay_in_their_row() -> None:
    raw = b'id,note,n\nA-1,"a, b ""c""",1\nA-2,"two\nlines",2\nA-3,x,3\n'
    found = rows(parse(raw))
    assert found[1][1] == 'id=A-1, note=a, b "c", n=1'
    assert found[2][1] == "id=A-2, note=two\nlines, n=2"
    # the line is where the row starts in the file, so a multi-line value does not shift the next
    assert [item[4] for item in found] == [1, 2, 3, 5]
    assert [item[3] for item in found] == ["/header", "/rows/2", "/rows/3", "/rows/4"]


def test_a_byte_order_mark_is_not_part_of_the_first_column_name() -> None:
    document = parse("\ufeffid,value\nA-1,5\n".encode())
    assert [node.text for node in document.nodes] == ["id, value", "id=A-1, value=5"]


def test_a_file_without_a_header_row_is_read_with_column_numbers() -> None:
    document = parse(b"A-1,5,rain\nA-2,7,dry\n")
    assert [node.kind for node in document.nodes] == [Kind.ROW, Kind.ROW]
    assert [node.text for node in document.nodes] == [
        "col1=A-1, col2=5, col3=rain",
        "col1=A-2, col2=7, col3=dry",
    ]
    assert [node.locator.json_pointer for node in document.nodes] == ["/rows/1", "/rows/2"]


def test_a_missing_column_name_falls_back_to_its_column_number() -> None:
    document = parse(b"id,,unit\nA-1,5,ms\n")
    assert document.nodes[1].text == "id=A-1, col2=5, unit=ms"


def test_a_short_row_is_a_candidate_and_is_read_with_its_neighbors() -> None:
    document = parse(b"unit,count\nms,12\nms,13\nms,14\n")
    spans = _spans(document, "csv")
    assert min(len(span.exact_text) for span in spans) < 24
    found = lexical_candidates("What is the count in ms?", (), spans)
    assert {span.exact_text for span in found} >= {"unit=ms, count=12", "unit=ms, count=13"}
    anchor = next(span for span in spans if span.exact_text == "unit=ms, count=13")
    near = [span.exact_text for span in text_neighbors(anchor, spans)]
    assert "unit, count" in near  # the header comes with the row


def test_a_large_file_reads_a_row_with_the_row_before_and_after_and_the_header() -> None:
    body = "".join(f"T-{n:02d},{n % 2}\n" for n in range(40))
    spans = _spans(parse(("id,detected\n" + body).encode()), "big")
    anchor = next(span for span in spans if span.exact_text == "id=T-20, detected=0")
    assert {span.exact_text for span in text_neighbors(anchor, spans)} == {
        "id, detected",
        "id=T-19, detected=1",
        "id=T-20, detected=0",
        "id=T-21, detected=1",
    }
