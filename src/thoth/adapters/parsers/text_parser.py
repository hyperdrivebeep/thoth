from __future__ import annotations

from thoth.adapters.parsers.common import coverage, node_id, parser_artifact, validate_byte_hash
from thoth.domain.artifact import (
    ArtifactEnvelope,
    SourceLocator,
    StructuralDocument,
    StructuralNode,
)
from thoth.domain.enums import StructuralNodeKind


class TextParser:
    name = "text"
    version = "1.0.0"
    media_types = frozenset({"text/plain", "text/markdown"})
    suffixes = frozenset({".txt", ".md", ".markdown"})

    def parse(self, artifact: ArtifactEnvelope, raw: bytes) -> StructuralDocument:
        validate_byte_hash(artifact, raw)
        text = raw.decode("utf-8-sig")
        nodes: list[StructuralNode] = []
        paragraph = 0
        for line_number, line in enumerate(text.splitlines(), start=1):
            value = line.strip()
            if not value:
                continue
            paragraph += 1
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
                    locator=SourceLocator(line=line_number, paragraph=paragraph),
                )
            )
        return StructuralDocument(
            artifact=parser_artifact(artifact, name=self.name, version=self.version),
            nodes=tuple(nodes),
            extraction_coverage=coverage(nodes),
        )
