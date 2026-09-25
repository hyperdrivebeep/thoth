from __future__ import annotations

import io
from typing import Any

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from thoth.adapters.parsers.common import (
    coverage,
    node_id,
    parser_artifact,
    safe_zip,
    validate_byte_hash,
)
from thoth.domain.artifact import (
    ArtifactEnvelope,
    SourceLocator,
    StructuralDocument,
    StructuralNode,
)
from thoth.domain.enums import ParserErrorCode, StructuralNodeKind
from thoth.domain.errors import ParserFailure


class DocxParser:
    name = "docx"
    version = "1.0.0"
    media_types = frozenset(
        {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
    )
    suffixes = frozenset({".docx"})

    def parse(self, artifact: ArtifactEnvelope, raw: bytes) -> StructuralDocument:
        validate_byte_hash(artifact, raw)
        with safe_zip(raw) as archive:
            if "word/document.xml" not in archive.namelist():
                raise ParserFailure(
                    ParserErrorCode.MEDIA_TYPE_MISMATCH,
                    "DOCX container has no word/document.xml",
                )
        try:
            document: Any = Document(io.BytesIO(raw))
        except Exception as exc:
            raise ParserFailure(ParserErrorCode.CORRUPT_DOCUMENT, "invalid DOCX document") from exc

        nodes: list[StructuralNode] = []
        paragraph_number = 0
        table_number = 0
        for child in document.element.body.iterchildren():
            tag = str(child.tag)
            if tag.endswith("}p"):
                paragraph_number += 1
                paragraph = Paragraph(child, document)
                text = paragraph.text.strip()
                if text:
                    style = str(paragraph.style.name or "") if paragraph.style is not None else ""
                    kind = (
                        StructuralNodeKind.SECTION
                        if style.lower().startswith("heading")
                        else StructuralNodeKind.PARAGRAPH
                    )
                    nodes.append(
                        StructuralNode(
                            node_id=node_id(artifact, len(nodes), kind.value),
                            artifact_id=artifact.artifact_id,
                            kind=kind,
                            ordinal=len(nodes),
                            text=text,
                            locator=SourceLocator(
                                paragraph=paragraph_number, section=style or None
                            ),
                        )
                    )
            elif tag.endswith("}tbl"):
                table_number += 1
                table = Table(child, document)
                table_node_id = node_id(artifact, len(nodes), "TABLE")
                nodes.append(
                    StructuralNode(
                        node_id=table_node_id,
                        artifact_id=artifact.artifact_id,
                        kind=StructuralNodeKind.TABLE,
                        ordinal=len(nodes),
                        locator=SourceLocator(section=f"table:{table_number}"),
                    )
                )
                for row_number, row in enumerate(table.rows, start=1):
                    row_node_id = node_id(artifact, len(nodes), "ROW")
                    nodes.append(
                        StructuralNode(
                            node_id=row_node_id,
                            artifact_id=artifact.artifact_id,
                            parent_id=table_node_id,
                            kind=StructuralNodeKind.ROW,
                            ordinal=len(nodes),
                            locator=SourceLocator(section=f"table:{table_number}"),
                        )
                    )
                    for column_number, cell in enumerate(row.cells, start=1):
                        text = cell.text.strip()
                        nodes.append(
                            StructuralNode(
                                node_id=node_id(artifact, len(nodes), "CELL"),
                                artifact_id=artifact.artifact_id,
                                parent_id=row_node_id,
                                kind=StructuralNodeKind.CELL,
                                ordinal=len(nodes),
                                text=text or None,
                                locator=SourceLocator(
                                    section=f"table:{table_number}",
                                    cell_range=f"R{row_number}C{column_number}",
                                ),
                            )
                        )
        return StructuralDocument(
            artifact=parser_artifact(artifact, name=self.name, version=self.version),
            nodes=tuple(nodes),
            extraction_coverage=coverage(nodes),
        )
