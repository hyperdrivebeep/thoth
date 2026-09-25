from __future__ import annotations

import io
import zipfile
from typing import Any

from thoth.adapters.parsers.registry import default_parser_registry
from thoth.domain.enums import ParserErrorCode, StructuralNodeKind


def _hwpx_bytes() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "META-INF/container.xml",
            "<container><rootfiles>"
            '<rootfile full-path="Contents/content.hpf"/>'
            "</rootfiles></container>",
        )
        archive.writestr("Contents/header.xml", "<header/>")
        archive.writestr("Contents/content.hpf", "<manifest/>")
        archive.writestr(
            "Contents/section0.xml",
            """<section xmlns:hp="urn:hp">
            <hp:p><hp:run><hp:t>첫 문단</hp:t></hp:run></hp:p>
            <hp:tbl><hp:tr><hp:tc><hp:p><hp:run>
            <hp:t>셀 값</hp:t>
            </hp:run></hp:p></hp:tc></hp:tr></hp:tbl>
            <hp:chart />
            </section>""",
        )
        archive.writestr("BinData/image1.png", b"PNG")
    return output.getvalue()


def test_hwpx_preserves_paragraph_table_cell_and_unsupported_node(artifact_factory: Any) -> None:
    raw = _hwpx_bytes()
    artifact = artifact_factory(raw, "application/hwp+zip", ".hwpx")

    document = default_parser_registry().parse(artifact, raw)

    kinds = [node.kind for node in document.nodes]
    assert StructuralNodeKind.PARAGRAPH in kinds
    assert StructuralNodeKind.TABLE in kinds
    assert StructuralNodeKind.CELL in kinds
    assert StructuralNodeKind.UNSUPPORTED_ELEMENT in kinds
    cell = next(node for node in document.nodes if node.kind == StructuralNodeKind.CELL)
    assert cell.locator.cell_range == "R1C1"
    assert cell.locator.xml_path is not None
    assert any(issue.code == ParserErrorCode.UNSUPPORTED_ELEMENT for issue in document.warnings)
    assert document.artifact.byte_sha256 == artifact.byte_sha256
