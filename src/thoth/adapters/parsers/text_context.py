"""Observe Markdown-like block positions without rendering, running code or rewriting leaves."""

import hashlib
import re
from importlib.metadata import version

from markdown_it import MarkdownIt

from thoth.domain.artifact import ArtifactEnvelope, StructuralNode
from thoth.domain.context_observation import ContextObservation, ContextRegion


def observe_text_context(
    artifact: ArtifactEnvelope, text: str, nodes: tuple[StructuralNode, ...]
) -> ContextObservation:
    lines = text.splitlines(keepends=True)
    base = dict(
        project_id=artifact.project_id,
        artifact_id=artifact.artifact_id,
        byte_sha256=artifact.byte_sha256,
        line_count=len(lines),
        character_count=len(text),
    )
    if version("markdown-it-py") != "4.2.0":
        return ContextObservation.model_validate(
            {**base, "status": "UNAVAILABLE", "warnings": ("CONTEXT_OBSERVER_VERSION_UNSUPPORTED",)}
        )
    if any(c in text for c in "\v\f\x1c\x1d\x1e\x85\u2028\u2029"):
        return ContextObservation.model_validate(
            {**base, "status": "UNAVAILABLE", "warnings": ("CONTEXT_LINE_MAPPING_UNAVAILABLE",)}
        )
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    nodes_by_line: dict[int, list[str]] = {}
    for node in nodes:
        if node.locator.line is not None:
            nodes_by_line.setdefault(node.locator.line, []).append(node.node_id)
    regions: list[ContextRegion] = []
    containers: list[str] = []
    headings: dict[tuple[str, ...], list[ContextRegion]] = {}
    previous: dict[tuple[str, ...], ContextRegion] = {}
    warnings: set[str] = set()
    parser = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})
    for token in parser.parse(text):
        container_token = token.type not in {
            "paragraph_open",
            "paragraph_close",
            "heading_open",
            "heading_close",
        }
        if container_token and token.nesting == -1:
            containers.pop()
        path = tuple(containers)
        if token.map is not None and token.type in {"paragraph_open", "heading_open", "fence"}:
            start, end = token.map
            kind = "PARAGRAPH"
            level = None
            if token.type == "fence":
                kind = "FENCED_CODE"
            elif token.type == "heading_open" and token.markup.startswith("#"):
                kind, level = "ATX_HEADING", int(token.tag[1:])
            elif token.type == "heading_open":
                previous.pop(path, None)
                warnings.add("SETEXT_CONTEXT_NOT_OBSERVED")
                continue  # Setext is outside this observation contract.
            stack = headings.setdefault(path, [])
            if level is not None:
                while stack and (stack[-1].heading_level or 0) >= level:
                    stack.pop()
            parents = [
                h.region_id
                for depth in range(len(path) + 1)
                for h in headings.get(path[:depth], ())
            ]
            preceding = previous.get(path)
            leading = (
                (preceding.region_id,)
                if kind == "FENCED_CODE" and preceding is not None and preceding.kind == "PARAGRAPH"
                else ()
            )
            identifier = (
                "context:"
                + hashlib.sha256(
                    f"{artifact.artifact_id}\0{kind}\0{start}\0{end}".encode()
                ).hexdigest()[:24]
            )
            closure = "NOT_APPLICABLE"
            if kind == "FENCED_CODE":
                closed = (
                    not path
                    and end > start + 1
                    and bool(
                        re.fullmatch(
                            r" {0,3}"
                            + re.escape(token.markup[0])
                            + "{"
                            + str(len(token.markup))
                            + r",}[ \t]*",
                            lines[end - 1].rstrip("\r\n"),
                        )
                    )
                )
                closure = (
                    "CLOSED"
                    if closed
                    else "UNCLOSED"
                    if not path and end == len(lines)
                    else "UNKNOWN"
                )
                if closure != "CLOSED":
                    warnings.add(
                        "UNCLOSED_FENCED_CONTEXT"
                        if closure == "UNCLOSED"
                        else "FENCE_BOUNDARY_UNCONFIRMED"
                    )
            region = ContextRegion.model_validate(
                {
                    "region_id": identifier,
                    "kind": kind,
                    "line_start": start + 1,
                    "line_end": end + 1,
                    "char_start": offsets[start],
                    "char_end": offsets[end],
                    "node_refs": tuple(
                        identifier
                        for line_number in range(start + 1, end + 1)
                        for identifier in nodes_by_line.get(line_number, ())
                    ),
                    "heading_refs": tuple(dict.fromkeys(parents)),
                    "leading_refs": leading,
                    "container_path": path,
                    "heading_level": level,
                    "marker": token.markup,
                    "info": token.info,
                    "closure": closure,
                }
            )
            regions.append(region)
            previous[path] = region
            if level is not None:
                stack.append(region)
        elif (
            token.map is not None and token.block and token.nesting >= 0 and token.type != "inline"
        ):
            previous.pop(path, None)
            if token.type == "code_block":
                warnings.add("INDENTED_CODE_CONTEXT_NOT_OBSERVED")
        if container_token and token.nesting == 1:
            containers.append(f"{token.type}:{token.map}")
    blank_start: int | None = None
    for index in range(len(lines) + 1):
        blank = index < len(lines) and not lines[index].strip()
        if blank and blank_start is None:
            blank_start = index
        if not blank and blank_start is not None:
            regions.append(
                ContextRegion(
                    region_id="context:"
                    + hashlib.sha256(
                        f"{artifact.artifact_id}\0blank\0{blank_start}\0{index}".encode()
                    ).hexdigest()[:24],
                    kind="BLANK_INTERVAL",
                    line_start=blank_start + 1,
                    line_end=index + 1,
                    char_start=offsets[blank_start],
                    char_end=offsets[index],
                )
            )
            blank_start = None
    return ContextObservation.model_validate(
        {**base, "regions": tuple(regions), "warnings": tuple(sorted(warnings))}
    )
