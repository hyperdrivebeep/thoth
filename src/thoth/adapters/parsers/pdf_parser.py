from __future__ import annotations

import io

from pypdf import PageObject, PdfReader

from thoth.adapters.parsers.common import coverage, node_id, parser_artifact, validate_byte_hash
from thoth.adapters.parsers.document_time import (
    extract_declared_document_time,
    extract_pdf_time_observations,
)
from thoth.domain.artifact import (
    ArtifactEnvelope,
    ParseIssue,
    SourceLocator,
    StructuralDocument,
    StructuralNode,
)
from thoth.domain.enums import ParserErrorCode, StructuralNodeKind
from thoth.domain.errors import ParserFailure


def _is_fragmented(lines: list[str]) -> bool:
    """A page whose lines are almost all one or two characters long was split per glyph."""
    return len(lines) >= 24 and sum(len(line) <= 2 for line in lines) * 5 >= len(lines) * 4


def _recover_fragmented_page(page: PageObject, page_number: int, fragments: int) -> list[str]:
    try:
        layout_text = page.extract_text(extraction_mode="layout") or ""
    except Exception as exc:
        raise ParserFailure(
            ParserErrorCode.PARTIAL_EXTRACTION,
            f"fragmented PDF page {page_number} could not be recovered",
        ) from exc
    lines = [line.strip() for line in layout_text.splitlines() if line.strip()]
    # Accept the layout text only when it is far less fragmented and has real text lines.
    if len(lines) * 4 >= fragments or not any(len(line) >= 24 for line in lines):
        raise ParserFailure(
            ParserErrorCode.PARTIAL_EXTRACTION,
            f"fragmented PDF page {page_number} has no usable text lines",
        )
    return lines


class PdfParser:
    name = "pdf"
    version = "1.1.0"
    media_types = frozenset({"application/pdf"})
    suffixes = frozenset({".pdf"})

    def parse(self, artifact: ArtifactEnvelope, raw: bytes) -> StructuralDocument:
        validate_byte_hash(artifact, raw)
        try:
            reader = PdfReader(io.BytesIO(raw), strict=True)
        except Exception as exc:
            raise ParserFailure(ParserErrorCode.CORRUPT_DOCUMENT, "invalid PDF document") from exc
        if reader.is_encrypted:
            raise ParserFailure(ParserErrorCode.ENCRYPTED_DOCUMENT, "encrypted PDF is held")

        nodes: list[StructuralNode] = []
        warnings: list[ParseIssue] = []
        for page_number, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception as exc:
                raise ParserFailure(
                    ParserErrorCode.CORRUPT_DOCUMENT,
                    f"failed to extract PDF page {page_number}",
                ) from exc
            material_lines = [line.strip() for line in text.splitlines() if line.strip()]
            if _is_fragmented(material_lines):
                material_lines = _recover_fragmented_page(page, page_number, len(material_lines))
                warnings.append(
                    ParseIssue(
                        code=ParserErrorCode.PARTIAL_EXTRACTION,
                        message=(
                            "fragmented PDF text recovered with layout extraction; "
                            "verify quotations against the page"
                        ),
                        locator=SourceLocator(page=page_number),
                    )
                )
            if not material_lines:
                warnings.append(
                    ParseIssue(
                        code=ParserErrorCode.OCR_REQUIRED,
                        message="page has no embedded text; OCR is required",
                        locator=SourceLocator(page=page_number),
                    )
                )
                continue
            for line_number, value in enumerate(material_lines, start=1):
                nodes.append(
                    StructuralNode(
                        node_id=node_id(artifact, len(nodes), "PARAGRAPH"),
                        artifact_id=artifact.artifact_id,
                        kind=StructuralNodeKind.PARAGRAPH,
                        ordinal=len(nodes),
                        text=value,
                        locator=SourceLocator(page=page_number, line=line_number),
                    )
                )
        observations = (
            *extract_pdf_time_observations(reader),
            *extract_declared_document_time(nodes),
        )
        return StructuralDocument(
            artifact=parser_artifact(artifact, name=self.name, version=self.version),
            nodes=tuple(nodes),
            extraction_coverage=coverage(nodes),
            warnings=tuple(warnings),
            document_time_observations=observations,
        )
