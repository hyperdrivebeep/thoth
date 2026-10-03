from __future__ import annotations

import hashlib
import io
from typing import Any

import pytest
from pypdf import PageObject, PdfWriter
from reportlab.pdfgen.canvas import Canvas

from thoth.adapters.parsers.registry import default_parser_registry
from thoth.application.services.research_retrieval import lexical_candidates
from thoth.domain.artifact import SourceLocator
from thoth.domain.enums import (
    AuthorityState,
    CutoffState,
    ParserErrorCode,
    SupportState,
    VerificationState,
)
from thoth.domain.errors import ParserFailure
from thoth.domain.evidence import EvidenceSpan


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


def _fragmented_pdf() -> bytes:
    output = io.BytesIO()
    canvas = Canvas(output)
    x = 72
    for character in "PORT RFP 07-01 target detection in a restricted port area":
        canvas.drawString(x, 720, character)
        x += 7
    canvas.save()
    return output.getvalue()


def test_fragmented_pdf_text_remains_retrievable_with_page_locator(
    artifact_factory: Any,
) -> None:
    raw = _fragmented_pdf()
    artifact = artifact_factory(raw, "application/pdf", ".pdf")

    document = default_parser_registry().parse(artifact, raw)

    assert document.artifact.parser_version == "1.1.0"
    assert any(issue.code == ParserErrorCode.PARTIAL_EXTRACTION for issue in document.warnings)
    assert all(
        issue.locator is not None and issue.locator.page == 1
        for issue in document.warnings
        if issue.code == ParserErrorCode.PARTIAL_EXTRACTION
    )
    assert len(document.nodes) < 10
    assert all(node.locator.page == 1 for node in document.nodes)

    def span(identifier: str, artifact_id: str, text: str, line: int) -> EvidenceSpan:
        return EvidenceSpan(
            span_id=identifier,
            project_id="project:pdf-retrieval",
            artifact_id=artifact_id,
            source_version_id="source-version:pdf-retrieval",
            locator=SourceLocator(page=1, line=line),
            exact_text=text,
            text_sha256=hashlib.sha256(text.encode()).hexdigest(),
            extraction_method="pdf:1.1.0",
            support_state=SupportState.EXTRACTED,
            authority_state=AuthorityState.OFFICIAL,
            verification_state=VerificationState.SCHEMA_VALID,
            cutoff_state=CutoffState.ELIGIBLE,
        )

    evidence = [
        span(f"span:pdf-{i}", artifact.artifact_id, node.text or "", i)
        for i, node in enumerate(document.nodes, start=1)
    ]
    evidence.append(
        span(
            "span:announcement",
            "artifact:announcement",
            "Public announcement with an attachment list but no RFP body",
            1,
        )
    )
    selected = lexical_candidates("PORT RFP 07-01 target detection", (), tuple(evidence))
    assert any(item.artifact_id == artifact.artifact_id for item in selected)


def test_a_normal_multi_line_page_is_extracted_as_before(artifact_factory: Any) -> None:
    output = io.BytesIO()
    canvas = Canvas(output)
    for index in range(30):
        canvas.drawString(72, 800 - index * 20, f"Measured latency for run {index} was 12 ms")
    canvas.save()
    raw = output.getvalue()
    artifact = artifact_factory(raw, "application/pdf", ".pdf")

    document = default_parser_registry().parse(artifact, raw)

    assert len(document.nodes) == 30
    assert not any(issue.code == ParserErrorCode.PARTIAL_EXTRACTION for issue in document.warnings)


def test_fragmented_page_that_layout_extraction_cannot_recover_is_a_typed_failure(
    artifact_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = _fragmented_pdf()
    artifact = artifact_factory(raw, "application/pdf", ".pdf")
    original = PageObject.extract_text

    def no_layout(self: PageObject, *args: Any, **kwargs: Any) -> str:
        if kwargs.get("extraction_mode") == "layout":
            return ""
        return original(self, *args, **kwargs)

    monkeypatch.setattr(PageObject, "extract_text", no_layout)

    with pytest.raises(ParserFailure) as failure:
        default_parser_registry().parse(artifact, raw)
    assert failure.value.code == ParserErrorCode.PARTIAL_EXTRACTION


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
