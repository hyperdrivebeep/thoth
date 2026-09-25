from __future__ import annotations

import re
from collections.abc import Iterable
from xml.etree.ElementTree import Element, ParseError

from defusedxml.ElementTree import fromstring

from thoth.adapters.parsers.common import (
    coverage,
    node_id,
    parser_artifact,
    safe_zip,
    validate_byte_hash,
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

_SECTION_PATTERN = re.compile(r"^Contents/section(\d+)\.xml$", re.IGNORECASE)
_UNSUPPORTED = frozenset({"chart", "equation", "ole", "video"})


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _text_content(element: Element) -> str:
    parts: list[str] = []
    for descendant in element.iter():
        if _local_name(descendant.tag) in {"t", "text"} and descendant.text:
            parts.append(descendant.text)
    return "".join(parts).strip()


def _section_sort_key(name: str) -> int:
    match = _SECTION_PATTERN.match(name)
    return int(match.group(1)) if match else 2**31


class HwpxParser:
    name = "hwpx"
    version = "1.0.0"
    media_types = frozenset({"application/hwp+zip", "application/vnd.hancom.hwpx"})
    suffixes = frozenset({".hwpx"})

    def parse(self, artifact: ArtifactEnvelope, raw: bytes) -> StructuralDocument:
        validate_byte_hash(artifact, raw)
        nodes: list[StructuralNode] = []
        warnings: list[ParseIssue] = []
        with safe_zip(raw) as archive:
            names = archive.namelist()
            required = {"META-INF/container.xml", "Contents/header.xml"}
            if not required.issubset(names):
                raise ParserFailure(
                    ParserErrorCode.MEDIA_TYPE_MISMATCH,
                    "HWPX container is missing required metadata",
                )
            try:
                container = fromstring(archive.read("META-INF/container.xml"))
            except (ParseError, ValueError) as exc:
                raise ParserFailure(
                    ParserErrorCode.CORRUPT_DOCUMENT,
                    "malformed HWPX container.xml",
                ) from exc
            root_paths = {
                value
                for element in container.iter()
                for key, value in element.attrib.items()
                if key.rsplit("}", 1)[-1].lower() in {"full-path", "fullpath"}
            }
            if root_paths and not any(path in names for path in root_paths):
                warnings.append(
                    ParseIssue(
                        code=ParserErrorCode.PARTIAL_EXTRACTION,
                        message="declared HWPX content root is absent; ordered sections are used",
                    )
                )

            section_names = sorted(
                (name for name in names if _SECTION_PATTERN.match(name)),
                key=_section_sort_key,
            )
            if not section_names:
                raise ParserFailure(
                    ParserErrorCode.CORRUPT_DOCUMENT,
                    "HWPX container has no ordered section XML",
                )
            for section_number, section_name in enumerate(section_names, start=1):
                try:
                    root = fromstring(archive.read(section_name))
                except (ParseError, ValueError) as exc:
                    raise ParserFailure(
                        ParserErrorCode.CORRUPT_DOCUMENT,
                        f"malformed HWPX section: {section_name}",
                    ) from exc
                self._walk(
                    artifact,
                    root,
                    nodes,
                    warnings,
                    section_number=section_number,
                    xml_path=f"/{section_name}",
                    parent_id=None,
                )

            core_prefixes = ("Contents/", "META-INF/")
            for name in names:
                if name.endswith("/") or name.startswith(core_prefixes):
                    continue
                nodes.append(
                    StructuralNode(
                        node_id=node_id(artifact, len(nodes), "ATTACHMENT"),
                        artifact_id=artifact.artifact_id,
                        kind=StructuralNodeKind.ATTACHMENT,
                        ordinal=len(nodes),
                        text=name,
                        locator=SourceLocator(file_path=name),
                    )
                )
        return StructuralDocument(
            artifact=parser_artifact(artifact, name=self.name, version=self.version),
            nodes=tuple(nodes),
            extraction_coverage=coverage(nodes),
            warnings=tuple(warnings),
        )

    def _walk(
        self,
        artifact: ArtifactEnvelope,
        element: Element,
        nodes: list[StructuralNode],
        warnings: list[ParseIssue],
        *,
        section_number: int,
        xml_path: str,
        parent_id: str | None,
    ) -> None:
        children: Iterable[Element] = list(element)
        for index, child in enumerate(children, start=1):
            local = _local_name(child.tag)
            child_path = f"{xml_path}/{local}[{index}]"
            if local == "p":
                text = _text_content(child)
                if text:
                    nodes.append(
                        StructuralNode(
                            node_id=node_id(artifact, len(nodes), "PARAGRAPH"),
                            artifact_id=artifact.artifact_id,
                            parent_id=parent_id,
                            kind=StructuralNodeKind.PARAGRAPH,
                            ordinal=len(nodes),
                            text=text,
                            locator=SourceLocator(
                                section=f"section:{section_number}",
                                paragraph=sum(
                                    1
                                    for node in nodes
                                    if node.kind == StructuralNodeKind.PARAGRAPH
                                    and node.locator.section == f"section:{section_number}"
                                )
                                + 1,
                                xml_path=child_path,
                            ),
                        )
                    )
                self._walk(
                    artifact,
                    child,
                    nodes,
                    warnings,
                    section_number=section_number,
                    xml_path=child_path,
                    parent_id=parent_id,
                )
            elif local == "tbl":
                table_id = node_id(artifact, len(nodes), "TABLE")
                nodes.append(
                    StructuralNode(
                        node_id=table_id,
                        artifact_id=artifact.artifact_id,
                        parent_id=parent_id,
                        kind=StructuralNodeKind.TABLE,
                        ordinal=len(nodes),
                        locator=SourceLocator(
                            section=f"section:{section_number}", xml_path=child_path
                        ),
                    )
                )
                self._table(
                    artifact,
                    child,
                    nodes,
                    section_number=section_number,
                    xml_path=child_path,
                    table_id=table_id,
                )
            elif local in _UNSUPPORTED:
                nodes.append(
                    StructuralNode(
                        node_id=node_id(artifact, len(nodes), "UNSUPPORTED_ELEMENT"),
                        artifact_id=artifact.artifact_id,
                        parent_id=parent_id,
                        kind=StructuralNodeKind.UNSUPPORTED_ELEMENT,
                        ordinal=len(nodes),
                        text=local,
                        locator=SourceLocator(
                            section=f"section:{section_number}", xml_path=child_path
                        ),
                        extraction_warnings=("UNSUPPORTED_ELEMENT",),
                    )
                )
                warnings.append(
                    ParseIssue(
                        code=ParserErrorCode.UNSUPPORTED_ELEMENT,
                        message=f"HWPX element {local!r} is preserved but not interpreted",
                        locator=SourceLocator(xml_path=child_path),
                    )
                )
            else:
                self._walk(
                    artifact,
                    child,
                    nodes,
                    warnings,
                    section_number=section_number,
                    xml_path=child_path,
                    parent_id=parent_id,
                )

    def _table(
        self,
        artifact: ArtifactEnvelope,
        table: Element,
        nodes: list[StructuralNode],
        *,
        section_number: int,
        xml_path: str,
        table_id: str,
    ) -> None:
        rows = [element for element in table.iter() if _local_name(element.tag) == "tr"]
        for row_number, row in enumerate(rows, start=1):
            row_id = node_id(artifact, len(nodes), "ROW")
            nodes.append(
                StructuralNode(
                    node_id=row_id,
                    artifact_id=artifact.artifact_id,
                    parent_id=table_id,
                    kind=StructuralNodeKind.ROW,
                    ordinal=len(nodes),
                    locator=SourceLocator(
                        section=f"section:{section_number}",
                        xml_path=f"{xml_path}/tr[{row_number}]",
                    ),
                )
            )
            cells = [element for element in list(row) if _local_name(element.tag) == "tc"]
            for column_number, cell in enumerate(cells, start=1):
                nodes.append(
                    StructuralNode(
                        node_id=node_id(artifact, len(nodes), "CELL"),
                        artifact_id=artifact.artifact_id,
                        parent_id=row_id,
                        kind=StructuralNodeKind.CELL,
                        ordinal=len(nodes),
                        text=_text_content(cell) or None,
                        locator=SourceLocator(
                            section=f"section:{section_number}",
                            cell_range=f"R{row_number}C{column_number}",
                            xml_path=f"{xml_path}/tr[{row_number}]/tc[{column_number}]",
                        ),
                    )
                )
