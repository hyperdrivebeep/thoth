"""Translate the public Docling export into THOTH nodes, retaining relation uncertainty."""

import hashlib
import re
from collections.abc import Callable
from decimal import Decimal
from typing import Any, cast

from thoth.adapters.parsers.common import node_id, parser_artifact
from thoth.domain.artifact import (
    ArtifactEnvelope,
    ParseIssue,
    ParserCapabilityObservation,
    SourceLocator,
    StructuralDocument,
    StructuralNode,
    StructuralRelation,
)
from thoth.domain.enums import ParserErrorCode
from thoth.domain.enums import StructuralNodeKind as Kind

TABLE_LABEL = re.compile(r"^(?:Table|표)\s+([A-Za-z]?\d+)[.:]\s", re.I)
TABLE_MENTION = re.compile(r"\bTable\s+([A-Za-z]?\d+)\b", re.I)


def _segment_node_id(
    artifact: ArtifactEnvelope, pointer: str, index: int, start: int, end: int
) -> str:
    payload = f"{artifact.artifact_id}\0{pointer}\0{index}\0{start}\0{end}\0DOCLING".encode()
    return f"node:{hashlib.sha256(payload).hexdigest()[:24]}"


def _charspan(provenance: dict[str, Any]) -> tuple[int, int] | None:
    raw = provenance.get("charspan", provenance.get("char_span"))
    if isinstance(raw, dict):
        raw_map = cast(dict[str, object], raw)
        start = raw_map.get("start", raw_map.get("char_start"))
        end = raw_map.get("end", raw_map.get("char_end"))
    elif isinstance(raw, list):
        raw_list = cast(list[object], raw)
        if len(raw_list) != 2:
            return None
        start, end = raw_list
    elif isinstance(raw, tuple):
        raw_tuple = cast(tuple[object, ...], raw)
        if len(raw_tuple) != 2:
            return None
        start, end = raw_tuple
    else:
        start = provenance.get("char_start")
        end = provenance.get("char_end")
    if isinstance(start, int) and isinstance(end, int):
        return start, end
    return None


def _located_segments(
    text: str,
    provenance: list[dict[str, Any]],
) -> tuple[list[tuple[int, int, dict[str, Any]]] | None, str | None]:
    segments: list[tuple[int, int, dict[str, Any]]] = []
    for observed in provenance:
        span = _charspan(observed)
        if span is None:
            return None, "Docling multi-provenance item omitted a charspan"
        start, end = span
        if start < 0 or end <= start or end > len(text):
            return None, "Docling multi-provenance item reported an invalid charspan"
        segments.append((start, end, observed))
    segments.sort(key=lambda segment: (segment[0], segment[1]))
    previous_end = 0
    for start, end, _ in segments:
        if start < previous_end:
            return None, "Docling multi-provenance item reported overlapping charspans"
        if text[previous_end:start].strip():
            return None, "Docling multi-provenance item has unlocated non-whitespace text"
        previous_end = end
    if text[previous_end:].strip():
        return None, "Docling multi-provenance item has trailing unlocated text"
    return segments, None


def _text_coordinate_basis(item: dict[str, Any]) -> tuple[str, str | None]:
    text = item.get("text")
    orig = item.get("orig")
    if isinstance(text, str) and isinstance(orig, str) and text != orig:
        return "", "Docling text and orig differ; charspan coordinate basis is unverified"
    basis = orig if isinstance(orig, str) else text
    if isinstance(basis, str):
        return basis, None
    return "", None


def _validated_charspan(
    item: dict[str, Any],
    provenance: dict[str, Any],
) -> tuple[tuple[int, int] | None, str | None]:
    basis, error = _text_coordinate_basis(item)
    span = _charspan(provenance)
    if span is None:
        return None, None
    if error is not None:
        return None, error
    start, end = span
    if start < 0 or end <= start or end > len(basis):
        return None, "Docling provenance charspan is outside the item text"
    return span, None


def _source_locator(
    export: dict[str, Any],
    item: dict[str, Any],
    pointer: str,
    *,
    provenance: dict[str, Any] | None = None,
    char_range: tuple[int, int] | None = None,
) -> SourceLocator:
    prov = item.get("prov", []) if provenance is None else [provenance]
    if len(prov) != 1:
        return SourceLocator(json_pointer=pointer)
    page = prov[0]["page_no"]
    box = prov[0].get("bbox")
    bbox = None
    if box:
        height = export.get("pages", {}).get(str(page), {}).get("size", {}).get("height")
        if box.get("coord_origin") == "TOPLEFT":
            bbox = (box["l"], box["t"], box["r"], box["b"])
        elif height is not None:
            bbox = (box["l"], height - box["t"], box["r"], height - box["b"])
    char_start = None
    char_end = None
    if char_range is not None:
        char_start, char_end = char_range
    return SourceLocator(
        page=page,
        char_start=char_start,
        char_end=char_end,
        bbox=bbox,
        json_pointer=pointer,
    )


def _append_explicit_table_mentions(nodes: list[StructuralNode]) -> None:
    label_targets: dict[str, set[str]] = {}
    for node in nodes:
        match = TABLE_LABEL.match(node.text or "")
        if match:
            label_targets.setdefault(match.group(1).lower(), set()).update(
                r.target_id for r in node.relations if r.kind == "CAPTION_FOR"
            )
    for index, node in enumerate(nodes):
        if node.kind != Kind.PARAGRAPH or not node.text:
            continue
        targets = {
            next(iter(label_targets[m.lower()]))
            for m in TABLE_MENTION.findall(node.text)
            if len(label_targets.get(m.lower(), set())) == 1
        }
        if targets:
            existing = {
                (relation.kind, relation.target_id, relation.basis): relation
                for relation in node.relations
            }
            mention_relations = tuple(
                StructuralRelation(kind="MENTIONS", target_id=t, basis="EXPLICIT_LABEL")
                for t in sorted(targets)
                if ("MENTIONS", t, "EXPLICIT_LABEL") not in existing
            )
            nodes[index] = node.model_copy(
                update={
                    "kind": Kind.MENTION,
                    "relations": node.relations + mention_relations,
                }
            )


def _append_coverage_warnings(
    nodes: list[StructuralNode], warnings: list[ParseIssue], *, partial: bool
) -> None:
    if not any(n.text for n in nodes):
        warnings.append(
            ParseIssue(
                code=ParserErrorCode.OCR_REQUIRED,
                message="No text was observed; OCR is not enabled in this capability",
            )
        )
    if partial:
        warnings.append(
            ParseIssue(
                code=ParserErrorCode.PARTIAL_EXTRACTION,
                message="Docling reported partial conversion",
            )
        )


def convert_structure(
    artifact: ArtifactEnvelope,
    export: dict[str, Any],
    *,
    parser_name: str,
    parser_version: str,
    configuration_digest: str,
    asset_digest: str,
    partial: bool = False,
) -> StructuralDocument:
    items = [
        export["body"],
        export["furniture"],
        *export.get("groups", []),
        *export.get("texts", []),
        *export.get("tables", []),
    ]
    refs = {item["self_ref"]: node_id(artifact, i, "DOCLING") for i, item in enumerate(items)}
    nodes: list[StructuralNode] = []
    warnings: list[ParseIssue] = []

    def locator(
        item: dict[str, Any],
        pointer: str,
        *,
        provenance: dict[str, Any] | None = None,
        char_range: tuple[int, int] | None = None,
    ) -> SourceLocator:
        return _source_locator(
            export, item, pointer, provenance=provenance, char_range=char_range
        )

    captions: dict[str, list[tuple[str, str]]] = {}
    for table in export.get("tables", []):
        for field, relation in [("captions", "CAPTION_FOR"), ("footnotes", "FOOTNOTE_FOR")]:
            for reference in table.get(field, []):
                if reference.get("$ref") in refs:
                    captions.setdefault(reference["$ref"], []).append(
                        (relation, refs[table["self_ref"]])
                    )
    for item in items:
        ref = item["self_ref"]
        label = item.get("label", "group")
        text = item.get("orig", item.get("text"))
        kind = {
            "table": Kind.TABLE,
            "section_header": Kind.SECTION,
            "caption": Kind.CAPTION,
            "footnote": Kind.FOOTNOTE,
            "page_header": Kind.PARAGRAPH,
            "page_footer": Kind.PARAGRAPH,
            "title": Kind.SECTION,
        }.get(label, Kind.PARAGRAPH if text else Kind.SECTION)
        relations: list[StructuralRelation] = []
        for relation, target in captions.get(ref, []):
            relations.append(
                StructuralRelation.model_validate({"kind": relation, "target_id": target})
            )
            kind = Kind.CAPTION if relation == "CAPTION_FOR" else Kind.FOOTNOTE
        if text and TABLE_LABEL.match(text):
            kind = Kind.CAPTION
        extraction_warnings = (
            (f"SOURCE_LABEL:{label}",) if item.get("content_layer") == "furniture" else ()
        )
        provenance = item.get("prov", [])
        if text and len(provenance) > 1:
            coordinate_text, coordinate_error = _text_coordinate_basis(item)
            segments, reason = (
                (None, coordinate_error)
                if coordinate_error is not None
                else _located_segments(coordinate_text or text, provenance)
            )
            if segments is None:
                node_locator = SourceLocator(json_pointer=ref)
                nodes.append(
                    StructuralNode(
                        node_id=refs[ref],
                        artifact_id=artifact.artifact_id,
                        kind=kind,
                        parent_id=refs.get(item.get("parent", {}).get("$ref")),
                        ordinal=len(nodes),
                        text=text,
                        locator=node_locator,
                        relations=tuple(relations),
                        extraction_warnings=(
                            *extraction_warnings,
                            "MULTI_PROVENANCE_UNLOCATED",
                        ),
                    )
                )
                warnings.append(
                    ParseIssue(
                        code=ParserErrorCode.PARTIAL_EXTRACTION,
                        message=reason or "Docling multi-provenance item could not be located",
                        locator=node_locator,
                    )
                )
                continue
            first_id = refs[ref]
            for segment_index, (start, end, observed) in enumerate(segments):
                segment_id = (
                    first_id
                    if segment_index == 0
                    else _segment_node_id(artifact, ref, segment_index, start, end)
                )
                segment_relations = tuple(relations)
                if segment_index > 0:
                    segment_relations = (
                        *segment_relations,
                        StructuralRelation(kind="CONTINUATION_OF", target_id=first_id),
                    )
                nodes.append(
                    StructuralNode(
                        node_id=segment_id,
                        artifact_id=artifact.artifact_id,
                        kind=kind,
                        parent_id=refs.get(item.get("parent", {}).get("$ref")),
                        ordinal=len(nodes),
                        text=text[start:end],
                        locator=locator(
                            item,
                            ref,
                            provenance=observed,
                            char_range=(start, end),
                        ),
                        relations=segment_relations,
                        extraction_warnings=extraction_warnings,
                    )
                )
            continue
        char_range = None
        char_warning = None
        if text and len(provenance) == 1:
            char_range, char_warning = _validated_charspan(item, provenance[0])
            if char_warning is not None:
                warnings.append(
                    ParseIssue(
                        code=ParserErrorCode.PARTIAL_EXTRACTION,
                        message=char_warning,
                        locator=locator(item, ref),
                    )
                )
        nodes.append(
            StructuralNode(
                node_id=refs[ref],
                artifact_id=artifact.artifact_id,
                kind=kind,
                parent_id=refs.get(item.get("parent", {}).get("$ref")),
                ordinal=len(nodes),
                text=text,
                locator=locator(
                    item,
                    ref,
                    char_range=char_range,
                ),
                relations=tuple(relations),
                extraction_warnings=(
                    (*extraction_warnings, "CHARSPAN_UNLOCATED")
                    if char_warning is not None
                    else extraction_warnings
                ),
            )
        )

    _infer_caption_links(export, refs, nodes, warnings)
    _promote_unique_caption_labels(nodes)
    _append_table_cells(artifact, export, refs, nodes, warnings, locator)

    _append_explicit_table_mentions(nodes)
    _append_coverage_warnings(nodes, warnings, partial=partial)
    coverage = "PARTIAL_STRUCTURE" if warnings else "OBSERVED_STRUCTURE"
    return StructuralDocument(
        artifact=parser_artifact(artifact, name=parser_name, version=parser_version),
        nodes=tuple(nodes),
        extraction_coverage=coverage,
        warnings=tuple(warnings),
        capability_observation=ParserCapabilityObservation(
            parser_name=parser_name,
            parser_version=parser_version,
            declared_capabilities=(
                "TEXT",
                "TABLE",
                "HEADER",
                "CAPTION",
                "FOOTNOTE",
                "UNIT",
                "MENTION",
            ),
            configuration_digest=configuration_digest,
            asset_manifest_digest=asset_digest,
            extraction_coverage=coverage,
        ),
    )


def _infer_caption_links(
    export: dict[str, Any],
    refs: dict[str, str],
    nodes: list[StructuralNode],
    warnings: list[ParseIssue],
) -> None:
    table_nodes = [n for n in nodes if n.kind == Kind.TABLE]
    cell_tops: dict[str, Decimal] = {}
    for table in export.get("tables", []):
        coordinates = [
            Decimal(str(cell["bbox"]["t"]))
            for cell in table.get("data", {}).get("table_cells", [])
            if cell.get("bbox") and cell["bbox"].get("coord_origin") == "TOPLEFT"
        ]
        if coordinates:
            cell_tops[refs[table["self_ref"]]] = min(coordinates)

    def data_top(table: StructuralNode) -> Decimal:
        assert table.locator.bbox is not None
        return cell_tops.get(table.node_id, table.locator.bbox[1])

    # Unlinked native table labels remain explicitly inferred candidates.
    # Geometric association is never exported as a verified table relation.
    for index, caption in enumerate(nodes):
        if (
            caption.kind != Kind.CAPTION
            or caption.relations
            or not caption.text
            or not TABLE_LABEL.match(caption.text)
        ):
            continue
        if caption.locator.bbox is None:
            continue
        left, _, right, bottom = caption.locator.bbox
        options = [
            t
            for t in table_nodes
            if t.locator.page == caption.locator.page
            and t.locator.bbox is not None
            and t.locator.bbox[0] <= (left + right) / 2 <= t.locator.bbox[2]
            and data_top(t) >= bottom
        ]
        options.sort(key=data_top)
        tops = [data_top(t) for t in options]
        if options and (len(tops) == 1 or tops[0] != tops[1]):
            nodes[index] = caption.model_copy(
                update={
                    "relations": (
                        StructuralRelation(
                            kind="CAPTION_FOR", target_id=options[0].node_id, basis="INFERRED"
                        ),
                    ),
                    "extraction_warnings": (*caption.extraction_warnings, "CAPTION_LINK_INFERRED"),
                }
            )
            warnings.append(
                ParseIssue(
                    code=ParserErrorCode.PARTIAL_EXTRACTION,
                    message="A table-label relation requires semantic confirmation",
                    locator=caption.locator,
                )
            )


def _promote_unique_caption_labels(nodes: list[StructuralNode]) -> None:
    labeled: list[tuple[int, str]] = []
    for index, node in enumerate(nodes):
        if node.kind != Kind.CAPTION or not node.text:
            continue
        match = TABLE_LABEL.match(node.text)
        if match:
            labeled.append((index, match.group(1).lower()))
    counts: dict[str, int] = {}
    for _, label in labeled:
        counts[label] = counts.get(label, 0) + 1
    for index, label in labeled:
        if counts[label] != 1:
            continue
        node = nodes[index]
        if len(node.relations) != 1:
            continue
        relation = node.relations[0]
        if relation.kind != "CAPTION_FOR" or relation.basis != "INFERRED":
            continue
        nodes[index] = node.model_copy(
            update={
                "relations": (
                    StructuralRelation(
                        kind="CAPTION_FOR",
                        target_id=relation.target_id,
                        basis="EXPLICIT_LABEL",
                    ),
                )
            }
        )


def _append_table_cells(
    artifact: ArtifactEnvelope,
    export: dict[str, Any],
    refs: dict[str, str],
    nodes: list[StructuralNode],
    warnings: list[ParseIssue],
    locator: Callable[[dict[str, Any], str], SourceLocator],
) -> None:
    for table in export.get("tables", []):
        table_id = refs[table["self_ref"]]
        table_loc = locator(table, table["self_ref"])
        cells = table.get("data", {}).get("table_cells", [])
        if cells and table_loc.page is None:
            warnings.append(
                ParseIssue(
                    code=ParserErrorCode.PARTIAL_EXTRACTION,
                    message="Cell page locations are unavailable for this table",
                    locator=table_loc,
                )
            )
        row_ids = {
            i: node_id(artifact, len(nodes) + i, "ROW")
            for i in range(table.get("data", {}).get("num_rows", 0))
        }
        for row, row_id in row_ids.items():
            nodes.append(
                StructuralNode(
                    node_id=row_id,
                    artifact_id=artifact.artifact_id,
                    kind=Kind.ROW,
                    parent_id=table_id,
                    ordinal=len(nodes),
                    text=None,
                    locator=table_loc.model_copy(update={"row_index": row}),
                    extraction_warnings=("LOGICAL_TABLE_ROW",),
                )
            )
        pending: list[StructuralNode] = []
        units: list[StructuralNode] = []
        for i, cell in enumerate(cells):
            row = cell["start_row_offset_idx"]
            col = cell["start_col_offset_idx"]
            box = cell.get("bbox")
            bbox = None
            if box and box.get("coord_origin") == "TOPLEFT":
                bbox = (box["l"], box["t"], box["r"], box["b"])
            kind = Kind.HEADER if cell.get("column_header") or cell.get("row_header") else Kind.CELL
            loc = SourceLocator(
                page=table_loc.page,
                bbox=bbox,
                row_index=row,
                column_index=col,
                json_pointer=f"{table['self_ref']}/data/table_cells/{i}",
                cell_range=f"R{row + 1}C{col + 1}:"
                f"R{cell['end_row_offset_idx']}C{cell['end_col_offset_idx']}",
            )
            identifier = node_id(artifact, len(nodes) + len(pending), "CELL")
            parent_row = row_ids.get(row, table_id)
            pending.append(
                StructuralNode(
                    node_id=identifier,
                    artifact_id=artifact.artifact_id,
                    kind=kind,
                    parent_id=parent_row,
                    ordinal=len(nodes) + len(pending),
                    text=cell.get("text", ""),
                    locator=loc,
                    relations=(
                        StructuralRelation(
                            kind="ROW_FOR", target_id=parent_row, basis="PARSER_OBSERVATION"
                        ),
                    ),
                    extraction_warnings=() if table_loc.page else ("CELL_PAGE_UNRESOLVED",),
                )
            )
            if kind == Kind.HEADER and re.search(
                r"\b(?:million|thousand|ms|seconds?|bytes?|[KMGT]B)\b|%", cell.get("text", ""), re.I
            ):
                units.append(
                    StructuralNode(
                        node_id="pending-unit",
                        artifact_id=artifact.artifact_id,
                        kind=Kind.UNIT,
                        parent_id=identifier,
                        ordinal=0,
                        text=cell["text"],
                        locator=loc,
                        relations=(
                            StructuralRelation(
                                kind="UNIT_FOR", target_id=identifier, basis="EXPLICIT_LABEL"
                            ),
                        ),
                        extraction_warnings=("UNIT_LABEL_ONLY_NOT_A_QUANTITY_INTERPRETATION",),
                    )
                )
        data_by_col: dict[int, list[str]] = {}
        for node in pending:
            col = node.locator.column_index
            if node.kind == Kind.CELL and col is not None:
                data_by_col.setdefault(col, []).append(node.node_id)
        for node in pending:
            col = node.locator.column_index
            if node.kind == Kind.HEADER and col is not None:
                header_links = tuple(
                    StructuralRelation(
                        kind="HEADER_FOR", target_id=cell_id, basis="PARSER_OBSERVATION"
                    )
                    for cell_id in data_by_col.get(col, ())
                )
                nodes.append(
                    node.model_copy(update={"relations": node.relations + header_links})
                )
            else:
                nodes.append(node)
        for unit in units:
            nodes.append(
                unit.model_copy(
                    update={
                        "node_id": node_id(artifact, len(nodes), "UNIT"),
                        "ordinal": len(nodes),
                    }
                )
            )
