"""The trace CSV carries a read-only note of each closed row; importing the file never reads it."""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

import pytest
from tests.integration.test_judgment_records import close
from tests.integration.test_research_request_v2 import ControlledResearchModel
from tests.integration.test_trace_origin import FOG, Rpc, opened
from tests.integration.trace_demo_helpers import demo_set, with_notice

from thoth.application.services.trace_csv import BOM, CLOSURE_COLUMN, export_csv


def rows_of(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text.removeprefix(BOM), newline="")))


async def exported(rpc: Rpc) -> dict[str, Any]:
    return await rpc("trace/export", project_id="p")


async def preview(rpc: Rpc, text: str) -> dict[str, Any]:
    return await rpc("trace/importPreview", project_id="p", mode="UPDATE", csv_text=text)


@pytest.mark.asyncio
async def test_a_set_without_closures_exports_the_same_bytes_as_before_and_has_no_column(
    tmp_path: Path,
) -> None:
    runtime, rpc = await opened(tmp_path, ControlledResearchModel())
    try:
        done = await exported(rpc)
        assert CLOSURE_COLUMN not in done["csv_text"].removeprefix(BOM).splitlines()[0]
        current = (await rpc("trace/read", project_id="p"))["set_digest"]
        assert (
            done["csv_text"] == export_csv(with_notice(demo_set(1)))
            and done["set_digest"] == current
        )
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_closed_row_is_noted_on_its_own_item_row_only_and_the_file_imports_with_no_change(
    tmp_path: Path,
) -> None:
    runtime, rpc = await opened(tmp_path, ControlledResearchModel())
    try:
        await close(rpc, FOG, "FIX_APPLIED")
        await close(rpc, FOG, "WAIVER_RECORDED", basis_ref="deviation DEV-4")
        text = (await exported(rpc))["csv_text"]
        assert text.removeprefix(BOM).splitlines()[0].endswith(CLOSURE_COLUMN)
        marked = [row for row in rows_of(text) if row.get(CLOSURE_COLUMN)]
        # the closure's subject id is the id of the CRITERION item, so the note lands on that row
        assert [(row["row_type"], row["item_id"], row["kind"]) for row in marked] == [
            ("ITEM", FOG, "CRITERION")
        ]
        assert marked[0][CLOSURE_COLUMN].startswith("편차·면제 승인(원 기준 미충족 유지)")
        assert "근거 문서: deviation DEV-4" in marked[0][CLOSURE_COLUMN]
        assert marked[0][CLOSURE_COLUMN].endswith("이전 기록 1건(THOTH에서 확인)")
        # the same file read back changes nothing and the column is not reported as ignored
        plan = await preview(rpc, text)
        assert plan["applicable"] and plan["conflicts"] == []
        assert [len(plan["changes"][name]) for name in ("added", "updated", "deleted")] == [0, 0, 0]
        assert not any("column ignored" in line for line in plan["losses"])
        assert any(line.startswith("closures:") for line in plan["losses"])
        # editing the cell changes nothing either
        edited = text.replace(marked[0][CLOSURE_COLUMN], "효과 확인됨")
        assert edited != text
        again = await preview(rpc, edited)
        assert [len(again["changes"][name]) for name in ("added", "updated", "deleted")] == [
            0,
            0,
            0,
        ]
        assert again["changes"]["unchanged"] == plan["changes"]["unchanged"]
        # the closure itself is untouched by all of this
        found = (await rpc("trace/closure/list", project_id="p"))["closures"]
        assert [len(item["events"]) for item in found] == [2]
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_document_that_looks_like_a_formula_cannot_run_in_a_spreadsheet(
    tmp_path: Path,
) -> None:
    runtime, rpc = await opened(tmp_path, ControlledResearchModel())
    try:
        await close(rpc, FOG, "HUMAN_CLOSED", basis_ref='=HYPERLINK("http://x","y")')
        cell = next(
            row[CLOSURE_COLUMN]
            for row in rows_of((await exported(rpc))["csv_text"])
            if row.get(CLOSURE_COLUMN)
        )
        assert not cell.startswith(("=", "+", "-", "@", "\t", "\r", "'"))
        assert "HYPERLINK" in cell
        plan = await preview(rpc, (await exported(rpc))["csv_text"])
        assert plan["applicable"] and plan["changes"]["updated"] == []
    finally:
        runtime.close()
