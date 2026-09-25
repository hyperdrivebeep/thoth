from __future__ import annotations

import io

from pypdf import PdfReader

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


class PdfParser:
    name = "pdf"
    version = "1.0.0"
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
