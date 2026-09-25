from __future__ import annotations

import io
from typing import Any

from pypdf import PdfWriter
from reportlab.pdfgen.canvas import Canvas

from thoth.adapters.parsers.registry import default_parser_registry
from thoth.domain.enums import ParserErrorCode
from thoth.domain.errors import ParserFailure


def test_pdf_embedded_text_has_page_and_line_locator(artifact_factory: Any) -> None:
    output = io.BytesIO()
    canvas = Canvas(output)
    canvas.drawString(72, 720, "Target accuracy 94 percent")
    canvas.save()
    raw = output.getvalue()
    artifact = artifact_factory(raw, "application/pdf", ".pdf")

    document = default_parser_registry().parse(artifact, raw)

    assert document.nodes[0].locator.page == 1
    assert document.nodes[0].locator.line == 1
    assert "94" in (document.nodes[0].text or "")
    assert document.artifact.byte_sha256 == artifact.byte_sha256


def test_pdf_silent_empty_success_is_rejected_as_ocr_required(artifact_factory: Any) -> None:
    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(output)
    raw = output.getvalue()
    artifact = artifact_factory(raw, "application/pdf", ".pdf")

    document = default_parser_registry().parse(artifact, raw)

    assert document.extraction_coverage == "NONE"
    assert document.warnings[0].code == ParserErrorCode.OCR_REQUIRED


def test_encrypted_pdf_is_a_typed_hold(artifact_factory: Any) -> None:
    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.encrypt("secret")
    writer.write(output)
    raw = output.getvalue()
    artifact = artifact_factory(raw, "application/pdf", ".pdf")

    try:
        default_parser_registry().parse(artifact, raw)
    except ParserFailure as exc:
        assert exc.code == ParserErrorCode.ENCRYPTED_DOCUMENT
    else:
        raise AssertionError("encrypted PDF must not be silently parsed")
