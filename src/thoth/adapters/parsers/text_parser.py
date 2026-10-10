from __future__ import annotations

import re
from pathlib import PurePosixPath

from thoth.adapters.parsers.common import coverage, node_id, parser_artifact, validate_byte_hash
from thoth.domain.artifact import (
    ArtifactEnvelope,
    SourceLocator,
    StructuralDocument,
    StructuralNode,
)
from thoth.domain.enums import StructuralNodeKind

_YAML_SUFFIXES = frozenset({".yaml", ".yml"})
_KEY_LINE = re.compile(r"^(?P<indent> *)(?P<key>[A-Za-z0-9_][\w.\-]*)\s*:(?:\s|$)")
_LIST_ITEM = re.compile(r"^(?P<indent> *)-(?:\s|$)")


def _is_yaml(artifact: ArtifactEnvelope) -> bool:
    name = PurePosixPath(artifact.source_uri.replace("\\", "/"))
    return name.suffix.lower() in _YAML_SUFFIXES


class _KeyPath:
    """Track the indentation-based key path of a YAML file without interpreting its values."""

    def __init__(self) -> None:
        self._stack: list[tuple[int, str]] = []
        self._items: dict[str, int] = {}

    def pointer(self, line: str) -> str | None:
        key_line = _KEY_LINE.match(line)
        item = None if key_line else _LIST_ITEM.match(line)
        match = key_line or item
        if match is None:
            return None
        indent = len(match.group("indent"))
        while self._stack and self._stack[-1][0] >= indent:
            self._stack.pop()
        parent = "".join(f"/{key}" for _, key in self._stack)
        if key_line is not None:
            key = key_line.group("key")
            self._stack.append((indent, key))
            return f"{parent}/{key}"
        index = self._items.get(parent, 0)
        self._items[parent] = index + 1
        return f"{parent}/{index}"


class TextParser:
    name = "text"
    version = "1.1.0"
    media_types = frozenset({"text/plain", "text/markdown"})
    # .yaml/.yml are read as plain lines of text. Nothing in them is interpreted or run.
    suffixes = frozenset({".txt", ".md", ".markdown", ".yaml", ".yml"})

    def parse(self, artifact: ArtifactEnvelope, raw: bytes) -> StructuralDocument:
        validate_byte_hash(artifact, raw)
        text = raw.decode("utf-8-sig")
        # A YAML line keeps its key path (for example /detection/denominator) so a block of short
        # key-value lines can be read together. Other text files are left as flat lines.
        key_path = _KeyPath() if _is_yaml(artifact) else None
        nodes: list[StructuralNode] = []
        paragraph = 0
        for line_number, line in enumerate(text.splitlines(), start=1):
            value = line.strip()
            if not value:
                continue
            paragraph += 1
            pointer = None if key_path is None or value.startswith("#") else key_path.pointer(line)
            kind = (
                StructuralNodeKind.SECTION
                if value.startswith("#")
                else StructuralNodeKind.PARAGRAPH
            )
            nodes.append(
                StructuralNode(
                    node_id=node_id(artifact, len(nodes), kind.value),
                    artifact_id=artifact.artifact_id,
                    kind=kind,
                    ordinal=len(nodes),
                    text=value,
                    locator=SourceLocator(
                        line=line_number, paragraph=paragraph, json_pointer=pointer
                    ),
                )
            )
        return StructuralDocument(
            artifact=parser_artifact(artifact, name=self.name, version=self.version),
            nodes=tuple(nodes),
            extraction_coverage=coverage(nodes),
        )
