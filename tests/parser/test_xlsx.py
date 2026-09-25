from __future__ import annotations

import io
from typing import Any

from openpyxl import Workbook

from thoth.adapters.parsers.registry import default_parser_registry


def test_xlsx_preserves_sheet_cell_formula_and_hidden_state(artifact_factory: Any) -> None:
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "정량지표"
    sheet["A1"] = "목표"
    sheet["B1"] = 94
    sheet["C1"] = "=B1+1"
    hidden = book.create_sheet("내부산식")
    hidden.sheet_state = "hidden"
    hidden["A1"] = "비공개 보조값"
    output = io.BytesIO()
    book.save(output)
    raw = output.getvalue()
    artifact = artifact_factory(
        raw,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsx",
    )

    document = default_parser_registry().parse(artifact, raw)

    cells = {(node.locator.sheet, node.locator.cell_range): node for node in document.nodes}
    assert cells[("정량지표", "B1")].text == "94"
    assert cells[("정량지표", "C1")].text == "formula==B1+1; cached=None"
    assert "FORMULA_NOT_RECALCULATED" in cells[("정량지표", "C1")].extraction_warnings
    assert any(issue.locator and issue.locator.sheet == "내부산식" for issue in document.warnings)
    assert document.artifact.byte_sha256 == artifact.byte_sha256
