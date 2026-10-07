"""The closure_status cell of the trace CSV: one line of plain words per closed row, read only."""

from __future__ import annotations

from typing import Any

from tests.integration.trace_demo_helpers import demo_set, with_notice

from thoth.application.services.trace_closure_text import (
    closure_status_cells,
    closure_status_text,
    with_closure_notes,
)
from thoth.application.services.trace_csv import (
    BOM,
    CLOSURE_COLUMN,
    COLUMNS,
    LOSS_MANIFEST,
    export_csv,
    export_rows,
    parse_csv,
    write_csv,
)


def event(kind: str = "FIX_APPLIED", **patch: Any) -> dict[str, Any]:
    return {
        "event_id": "e",
        "subject_kind": "CRITERION",
        "subject_id": "C-1",
        "kind": kind,
        "basis_ref": "ECN-12",
        "note": "",
        "scope": None,
        "created_at": "2026-10-06T01:02:03+00:00",
        **patch,
    }


def row(*events: dict[str, Any], confirmed: bool = False, subject: str = "C-1") -> dict[str, Any]:
    return {
        "subject_kind": "CRITERION",
        "subject_id": subject,
        "effect_confirmed": confirmed,
        "events": list(events) or [event()],
    }


def test_each_kind_has_its_plain_words_with_the_document_and_the_date() -> None:
    assert closure_status_text(row()) == (
        "수정 반영됨(효과 미확인) · 근거 문서: ECN-12 · 기록 날짜: 2026-10-06"
    )
    assert closure_status_text(row(event("HUMAN_CLOSED"))).startswith(
        "사람 확인 종결(시험 결과 없음) · "
    )
    assert closure_status_text(row(event("WAIVER_RECORDED"))).startswith(
        "편차·면제 승인(원 기준 미충족 유지) · "
    )
    scoped = row(event("CONDITION_CHANGED", scope="50 m 이하 안개"))
    assert closure_status_text(scoped).startswith("운용 조건 변경(조건 범위: 50 m 이하 안개) · ")


def test_only_the_rule_may_say_the_effect_is_confirmed() -> None:
    assert closure_status_text(row(confirmed=True)).startswith("효과 확인됨 · 근거 문서: ECN-12")
    assert not closure_status_text(row()).startswith("효과 확인됨")


def test_the_latest_record_is_shown_and_the_earlier_ones_are_only_counted() -> None:
    shown = closure_status_text(
        row(
            event("HUMAN_CLOSED", basis_ref="OLD"),
            event("WAIVER_RECORDED", basis_ref="DEV-4", created_at="2026-10-07T00:00:00+00:00"),
        )
    )
    assert shown == (
        "편차·면제 승인(원 기준 미충족 유지) · 근거 문서: DEV-4 · 기록 날짜: 2026-10-07"
        " · 이전 기록 1건(THOTH에서 확인)"
    )
    assert "OLD" not in shown


def test_the_cells_are_keyed_by_the_row_and_hold_no_internal_code() -> None:
    cells = closure_status_cells([row(subject="C-1"), row(event("HUMAN_CLOSED"), subject="R-1")])
    assert set(cells) == {"C-1", "R-1"}
    for text in cells.values():
        assert "FIX_APPLIED" not in text and "HUMAN_CLOSED" not in text and "None" not in text
    assert closure_status_cells([]) == {}


def test_a_closure_column_appears_only_when_some_row_carries_a_value() -> None:
    trace_set = with_notice(demo_set(1))
    assert CLOSURE_COLUMN == "closure_status" and CLOSURE_COLUMN not in COLUMNS
    plain = export_csv(trace_set)
    assert CLOSURE_COLUMN not in plain.removeprefix(BOM).splitlines()[0]
    assert write_csv(export_rows(trace_set)) == plain
    criterion = next(item.item_id for item in trace_set.items if item.kind.value == "CRITERION")
    rows = with_closure_notes(
        export_rows(trace_set),
        {criterion: "효과 확인됨 · 근거 문서: ECN-12 · 기록 날짜: 2026-10-06"},
    )
    marked = [item for item in rows if item.get(CLOSURE_COLUMN)]
    assert [(item["row_type"], item["item_id"]) for item in marked] == [("ITEM", criterion)]
    assert CLOSURE_COLUMN in write_csv(rows).removeprefix(BOM).splitlines()[0]


def test_a_value_is_written_for_an_item_row_of_the_right_kind_only() -> None:
    trace_set = demo_set(1)
    by_kind = {item.item_id: item.kind.value for item in trace_set.items}
    test_case = next(key for key, kind in by_kind.items() if kind == "TEST_CASE")
    requirement = next(key for key, kind in by_kind.items() if kind == "REQUIREMENT")
    rows = with_closure_notes(export_rows(trace_set), {test_case: "x", requirement: "y"})
    assert [item["item_id"] for item in rows if item.get(CLOSURE_COLUMN)] == [requirement]


def test_the_column_is_known_but_never_read_back() -> None:
    trace_set = demo_set(1)
    criterion = next(item.item_id for item in trace_set.items if item.kind.value == "CRITERION")
    text = write_csv(
        with_closure_notes(
            export_rows(trace_set), {criterion: "사람 확인 종결(시험 결과 없음) · x"}
        )
    )
    parsed = parse_csv(text)
    assert parsed.issues == [] and parsed.ignored_columns == ()
    again = parse_csv(text.replace("사람 확인 종결(시험 결과 없음) · x", "효과 확인됨"))
    assert [item.model for item in parsed.rows] == [item.model for item in again.rows]


def test_a_value_that_a_spreadsheet_would_run_is_written_with_the_apostrophe() -> None:
    trace_set = demo_set(1)
    criterion = next(item.item_id for item in trace_set.items if item.kind.value == "CRITERION")
    text = write_csv(
        with_closure_notes(export_rows(trace_set), {criterion: '=HYPERLINK("http://x","y")'})
    )
    assert "'=HYPERLINK" in text and ",=HYPERLINK" not in text


def test_the_loss_list_says_the_column_is_for_reading_only() -> None:
    line = next(item for item in LOSS_MANIFEST if item.startswith("closures:"))
    assert "closure_status" in line and "reading only" in line
