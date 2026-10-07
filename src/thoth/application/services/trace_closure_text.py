"""The closure_status cell of the trace CSV: one line of plain words for each closed row.

The cell is a note for a person reading the file in a spreadsheet. It is built from the project's
recorded closures and is never read back by an import. Only the latest record is written, and the
number of earlier ones is counted; "effect confirmed" is said only when the rules confirmed it.
The kind names are the ones the screen uses for the same records.

The file carries the note in an optional column, closure_status, that is added only when some
requirement or criterion row has a note. The reader knows the column and never takes a value from
it, so editing it changes nothing.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import cast

_KIND_TEXT = {
    "FIX_APPLIED": "수정 반영됨(효과 미확인)",
    "HUMAN_CLOSED": "사람 확인 종결(시험 결과 없음)",
    "WAIVER_RECORDED": "편차·면제 승인(원 기준 미충족 유지)",
}
_EFFECT_CONFIRMED = "효과 확인됨"
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}")

CLOSURE_COLUMN = "closure_status"
ClosureNotes = Mapping[str, str]  # item id -> the note of that row
_CLOSABLE = ("REQUIREMENT", "CRITERION")  # the item kinds a verdict, and so a closure, belongs to


def _label(event: Mapping[str, object], confirmed: bool) -> str:
    if confirmed:
        return _EFFECT_CONFIRMED
    kind = str(event.get("kind", ""))
    if kind == "CONDITION_CHANGED":
        return f"운용 조건 변경(조건 범위: {event.get('scope') or '미기재'})"
    return _KIND_TEXT.get(kind, "처분 기록됨")


def closure_status_text(row: Mapping[str, object]) -> str:
    """The note for one row of TraceClosures.view: its latest record, and how many came before."""
    events = [
        cast("Mapping[str, object]", item)
        for item in cast("list[object]", row.get("events") or [])
        if isinstance(item, Mapping)
    ]
    latest = events[-1]
    day = _DAY.match(str(latest.get("created_at", "")))
    parts = [
        _label(latest, row.get("effect_confirmed") is True),
        f"근거 문서: {latest.get('basis_ref', '')}",
        *([f"기록 날짜: {day.group(0)}"] if day else []),
        *([f"이전 기록 {len(events) - 1}건(THOTH에서 확인)"] if len(events) > 1 else []),
    ]
    return " · ".join(parts)


def with_closure_notes(
    rows: list[dict[str, str]], closures: ClosureNotes | None
) -> list[dict[str, str]]:
    """The export rows with each note written on the requirement or criterion row it belongs to."""
    if not closures:
        return rows
    return [
        {**row, CLOSURE_COLUMN: closures[row["item_id"]]}
        if row["row_type"] == "ITEM" and row["kind"] in _CLOSABLE and row["item_id"] in closures
        else row
        for row in rows
    ]


def closure_status_cells(rows: Iterable[Mapping[str, object]]) -> dict[str, str]:
    """The notes by the row they belong to (a closure's subject id is that row's item id)."""
    return {
        str(row["subject_id"]): closure_status_text(row)
        for row in rows
        if row.get("events") and row.get("subject_id")
    }
