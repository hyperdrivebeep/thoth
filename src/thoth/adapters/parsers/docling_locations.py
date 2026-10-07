"""Locations inside a Docling export: character spans, boxes and segment ids.

These helpers turn the provenance of one exported item into THOTH locators. They read the export and
return values; they do not build nodes.
"""

import hashlib
from typing import Any, cast

from thoth.domain.artifact import ArtifactEnvelope, SourceLocator


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
