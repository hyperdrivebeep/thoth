"""The trace CSV format by itself: escaping, rows, spreadsheet damage, planning an import."""

from __future__ import annotations

import csv
import io

import pytest
from tests.integration.trace_demo_helpers import NOTICE, demo_set, with_notice

from thoth.application.services.trace_csv import (
    BOM,
    COLUMNS,
    FORMAT,
    escape_cell,
    export_csv,
    export_rows,
    parse_csv,
    spreadsheet_damage,
    spreadsheet_suspects,
    unescape_cell,
    write_csv,
)
from thoth.application.services.verification_trace_import import plan_import
from thoth.domain.verification_trace import (
    TraceItem,
    TraceKind,
    TraceSet,
    VerificationTraceRecord,
)

TEXTS = [
    "",
    "plain",
    "=1+1",
    "+SUM(A1)",
    "-5",
    "@cmd",
    "\tTab",
    "\rCR",
    "'=x",
    "''+x",
    "'",
    "'plain",
    "a'b",
    "=",
    "한글 =x",
    "-",
]


@pytest.mark.parametrize("text", TEXTS)
def test_escaping_is_undone_exactly(text: str) -> None:
    assert unescape_cell(escape_cell(text)) == text


def test_only_cells_a_spreadsheet_would_run_get_an_apostrophe() -> None:
    assert escape_cell("=1+1") == "'=1+1"
    assert escape_cell("-5") == "'-5"
    assert escape_cell("'=x") == "''=x"
    assert escape_cell("plain") == "plain"
    assert escape_cell("'plain") == "'plain"
    assert escape_cell("a=b") == "a=b"
    assert unescape_cell("'plain") == "'plain"


def with_row(**cells: str) -> list[str]:
    row = {name: "" for name in COLUMNS}
    row["format"] = FORMAT
    row.update(cells)
    return [row[name] for name in COLUMNS]


def text_of(rows: list[list[str]], header: list[str] | None = None) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(header or list(COLUMNS))
    writer.writerows(rows)
    return buffer.getvalue()


def test_export_starts_with_the_mark_uses_crlf_and_the_fixed_columns() -> None:
    text = export_csv(demo_set(1))
    assert text.startswith(BOM)
    assert "\r\n" in text and "\n" not in text.replace("\r\n", "")
    assert text.removeprefix(BOM).splitlines()[0] == ",".join(COLUMNS)


def test_what_is_exported_reads_back_as_the_same_things() -> None:
    original = demo_set(2)
    parsed = parse_csv(export_csv(original))
    assert parsed.issues == [] and parsed.base_digests == (original.set_digest,)
    by_type: dict[str, dict[str, object]] = {}
    for row in parsed.rows:
        by_type.setdefault(row.row_type, {})[row.key] = row.model
    assert by_type["ITEM"] == {item.item_id: item for item in original.items}
    assert by_type["LINK"] == {link.link_id: link for link in original.links}
    assert by_type["RULE"] == {rule.rule_id: rule for rule in original.rules}
    assert by_type["RESULT"] == {result.result_id: result for result in original.results}


def test_a_row_is_found_by_its_id_not_by_its_line() -> None:
    rows = [
        with_row(row_type="ITEM", item_id=name, item_key="k" + name, kind="REQUIREMENT")
        for name in ("B", "A")
    ]
    one = parse_csv(text_of(rows))
    two = parse_csv(text_of(list(reversed(rows))))
    assert {row.key for row in one.rows} == {row.key for row in two.rows} == {"A", "B"}
    assert [row.row for row in one.rows] == [2, 3]  # line numbers are for messages only


def codes(text: str) -> list[str]:
    return [issue.code for issue in parse_csv(text).issues]


def test_bad_files_and_rows_are_reported_and_never_raise() -> None:
    assert codes("") == ["CSV_EMPTY"]
    assert codes("a,b\r\n1,2\r\n") == ["HEADER_MISSING_COLUMNS"]
    duplicate = [*COLUMNS, "title"]
    assert codes(text_of([], header=duplicate)) == ["HEADER_DUPLICATE_COLUMN"]
    other = with_row(row_type="ITEM", item_id="A", kind="REQUIREMENT")
    other[0] = "other-csv/9"
    assert codes(text_of([other])) == ["FORMAT_UNSUPPORTED"]
    assert codes(text_of([with_row(row_type="WHAT", item_id="A")])) == ["ROW_TYPE_UNKNOWN"]
    assert codes(text_of([with_row(row_type="DELETE", target_kind="ITEM")])) == [
        "DELETE_ROW_INCOMPLETE"
    ]
    assert codes(text_of([with_row(row_type="DELETE", target_id="A")])) == ["DELETE_ROW_INCOMPLETE"]
    assert codes(text_of([with_row(row_type="ITEM", item_key="k", kind="REQUIREMENT")])) == [
        "ID_MISSING"
    ]
    assert codes(
        text_of([with_row(row_type="ITEM", item_id="A", item_key="k", kind="NOT_A_KIND")])
    ) == ["ROW_INVALID"]
    assert codes(
        text_of([with_row(row_type="LINK", link_id="L", from_id="A", to_id="B", relation="LOVES")])
    ) == ["ROW_INVALID"]
    assert codes(
        text_of(
            [
                with_row(
                    row_type="RULE",
                    rule_id="R",
                    criterion_id="C",
                    measure="m",
                    comparator="==",
                    threshold="1",
                    unit="u",
                    condition="c",
                    required="true",
                )
            ]
        )
    ) == ["ROW_INVALID"]
    assert codes(
        text_of(
            [
                with_row(
                    row_type="RULE",
                    rule_id="R",
                    criterion_id="C",
                    measure="m",
                    comparator=">=",
                    threshold="NaN",
                    unit="u",
                    condition="c",
                    required="true",
                )
            ]
        )
    ) == ["ROW_INVALID"]
    twice = [with_row(row_type="ITEM", item_id="A", item_key="k", kind="REQUIREMENT")] * 2
    assert codes(text_of(twice)) == ["DUPLICATE_ID_IN_FILE"]


def test_extra_columns_are_ignored_and_reported_and_short_rows_are_padded() -> None:
    header = [*COLUMNS, "my notes"]
    row = [*with_row(row_type="ITEM", item_id="A", item_key="k", kind="REQUIREMENT"), "hello"]
    parsed = parse_csv(text_of([row], header=header))
    assert parsed.issues == [] and parsed.ignored_columns == ("my notes",)
    padded = parse_csv(",".join(COLUMNS) + "\r\n" + FORMAT + ",ITEM\r\n")
    assert [issue.code for issue in padded.issues] == ["ID_MISSING"]


def test_an_item_with_no_key_gets_a_fixed_one_made_from_its_id() -> None:
    parsed = parse_csv(text_of([with_row(row_type="ITEM", item_id="A", kind="REQUIREMENT")]))
    (row,) = parsed.rows
    assert isinstance(row.model, TraceItem) and row.model.item_key.startswith("csv-")
    assert row.key_given is False


@pytest.mark.parametrize(
    ("original", "incoming", "reason"),
    [
        ("007", "7", "LEADING_ZEROS_LOST"),
        ("00", "0", "LEADING_ZEROS_LOST"),
        ("12345678901234567890", "1.23457E+19", "EXPONENT_FORM"),
        ("1-2", "2-Jan", "DATE_FORM"),
        ("2026-10", "Oct-26", "DATE_FORM"),
        ("2026-10", "2026-10-01", "DATE_FORM"),
        ("007", "007", None),
        ("A-1", "A-2", None),
        ("12", "1.2E+1", None),
        ("1-2", "7-Aug", None),
        ("123", "12", None),
    ],
)
def test_ids_a_spreadsheet_rewrites_are_recognised(
    original: str, incoming: str, reason: str | None
) -> None:
    assert spreadsheet_damage(original, incoming) == reason


def test_a_new_id_is_only_a_suspect_when_the_original_is_missing_from_the_file() -> None:
    assert spreadsheet_suspects({"007"}, {"7"}) == [("7", "007", "LEADING_ZEROS_LOST")]
    assert spreadsheet_suspects({"007"}, {"007", "7"}) == []
    assert spreadsheet_suspects({"007"}, {"8"}) == []


def record(*items: TraceItem) -> VerificationTraceRecord:
    return VerificationTraceRecord(project_id="p", trace_set=TraceSet(items=items))


def item(item_id: str, key: str, title: str = "") -> TraceItem:
    return TraceItem(item_id=item_id, item_key=key, kind=TraceKind.REQUIREMENT, title=title)


def test_update_needs_one_base_digest_that_matches_the_trace_now() -> None:
    current = record(item("A", "ka"))
    now = current.trace_set.set_digest

    def conflicts(*digests: str) -> set[str]:
        rows = [
            with_row(
                row_type="ITEM",
                item_id=f"I{number}",
                item_key=f"k{number}",
                kind="REQUIREMENT",
                base_set_digest=digest,
            )
            for number, digest in enumerate(digests)
        ]
        return {
            issue.code for issue in plan_import("p", current, "UPDATE", text_of(rows)).conflicts
        }

    assert conflicts("") == {"BASE_DIGEST_MISSING"}
    assert conflicts(now, "f" * 64) == {"BASE_DIGEST_INCONSISTENT"}
    assert conflicts("f" * 64) == {"STALE_BASE"}
    assert conflicts(now) == set()


def test_an_item_row_with_no_key_keeps_the_key_it_has_and_a_changed_key_is_refused() -> None:
    current = record(item("A", "ka", "old"))
    now = current.trace_set.set_digest
    blank = with_row(
        row_type="ITEM", item_id="A", kind="REQUIREMENT", title="new", base_set_digest=now
    )
    plan = plan_import("p", current, "UPDATE", text_of([blank]))
    assert plan.conflicts == [] and [change.fields for change in plan.updated] == [("title",)]
    assert plan.new_set is not None and plan.new_set.items[0].item_key == "ka"
    other = with_row(
        row_type="ITEM", item_id="A", item_key="zz", kind="REQUIREMENT", base_set_digest=now
    )
    assert [i.code for i in plan_import("p", current, "UPDATE", text_of([other])).conflicts] == [
        "ITEM_KEY_CHANGED"
    ]


def test_the_preview_id_follows_the_exact_text_and_the_trace_now() -> None:
    current = record(item("A", "ka"))
    now = current.trace_set.set_digest
    row = with_row(
        row_type="ITEM", item_id="A", item_key="ka", kind="REQUIREMENT", base_set_digest=now
    )
    text = text_of([row])
    same = plan_import("p", current, "UPDATE", text)
    assert same.preview_id == plan_import("p", current, "UPDATE", text).preview_id
    assert same.preview_id != plan_import("p", current, "UPDATE", text + "\r\n ,").preview_id
    assert same.preview_id != plan_import("p", record(item("B", "kb")), "UPDATE", text).preview_id
    assert same.preview_id != plan_import("q", current, "UPDATE", text).preview_id
    assert same.input_sha256 != plan_import("p", current, "UPDATE", BOM + text).input_sha256


def test_export_rows_can_be_written_and_read_by_other_tools() -> None:
    rows = export_rows(demo_set(1))
    read = list(csv.DictReader(io.StringIO(write_csv(rows).removeprefix(BOM), newline="")))
    assert read[0]["format"] == FORMAT and {row["row_type"] for row in read} == {
        "ITEM",
        "LINK",
        "RULE",
        "RESULT",
    }


def test_a_synthetic_notice_has_its_own_column_on_every_row_and_reads_back() -> None:
    original = with_notice(demo_set(1))
    text = export_csv(original)
    rows = list(csv.DictReader(io.StringIO(text.removeprefix(BOM), newline="")))
    assert list(rows[0]) == [*COLUMNS, "synthetic_notice"]
    assert {row["synthetic_notice"] for row in rows} == {NOTICE}
    assert all("synthetic_notice" not in row["fields_json"] for row in rows)
    parsed = parse_csv(text)
    assert parsed.issues == [] and parsed.ignored_columns == ()
    assert [row.model for row in parsed.rows if row.row_type == "ITEM"] == [
        item
        for item in sorted(original.items, key=lambda i: (list(TraceKind).index(i.kind), i.item_id))
    ]


def test_a_set_without_a_notice_gets_no_notice_column() -> None:
    header = export_csv(demo_set(1)).removeprefix(BOM).splitlines()[0]
    assert "synthetic_notice" not in header


def test_a_file_without_the_notice_column_still_imports_and_keeps_the_notice() -> None:
    current = VerificationTraceRecord(project_id="p", trace_set=with_notice(demo_set(1)))
    now = current.trace_set.set_digest
    row = with_row(
        row_type="ITEM",
        item_id="SYN-REQ-RAD-001",
        item_key="k-req",
        kind="REQUIREMENT",
        title="renamed",
        base_set_digest=now,
    )
    plan = plan_import("p", current, "UPDATE", text_of([row]))  # the old header has no such column
    assert plan.conflicts == [] and [change.fields for change in plan.updated] == [("title",)]
    assert plan.new_set is not None
    kept = next(i for i in plan.new_set.items if i.item_id == "SYN-REQ-RAD-001")
    assert kept.fields["synthetic_notice"] == NOTICE
    assert not any("synthetic_notice" in line for line in plan.losses)


def test_a_blank_notice_cell_cannot_remove_the_notice_and_a_filled_one_adds_it() -> None:
    current = VerificationTraceRecord(project_id="p", trace_set=with_notice(demo_set(1)))
    now = current.trace_set.set_digest
    header = [*COLUMNS, "synthetic_notice"]
    same = [
        *with_row(
            row_type="ITEM",
            item_id="SYN-REQ-RAD-001",
            item_key="k-req",
            kind="REQUIREMENT",
            title=next(i.title for i in current.trace_set.items if i.item_id == "SYN-REQ-RAD-001"),
            base_set_digest=now,
        ),
        "",
    ]
    plan = plan_import("p", current, "UPDATE", text_of([same], header=header))
    assert plan.conflicts == [] and plan.updated == [] and plan.unchanged == 1
    fresh = [
        *with_row(
            row_type="ITEM",
            item_id="NEW-1",
            item_key="knew",
            kind="REQUIREMENT",
            base_set_digest=now,
        ),
        NOTICE,
    ]
    added = plan_import("p", current, "UPDATE", text_of([fresh], header=header))
    assert added.new_set is not None
    assert next(i for i in added.new_set.items if i.item_id == "NEW-1").fields == {
        "synthetic_notice": NOTICE
    }
