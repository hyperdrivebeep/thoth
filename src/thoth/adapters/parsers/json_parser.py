from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import cast

from thoth.adapters.parsers.common import coverage, node_id, parser_artifact, validate_byte_hash
from thoth.domain.artifact import (
    ArtifactEnvelope,
    SourceLocator,
    StructuralDocument,
    StructuralNode,
)
from thoth.domain.enums import ParserErrorCode, StructuralNodeKind
from thoth.domain.errors import ParserFailure


def _escape_pointer(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


class JsonParser:
    name = "json"
    version = "1.0.0"
    media_types = frozenset({"application/json"})
    suffixes = frozenset({".json"})

    def parse(self, artifact: ArtifactEnvelope, raw: bytes) -> StructuralDocument:
        validate_byte_hash(artifact, raw)
        try:
            value = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ParserFailure(ParserErrorCode.CORRUPT_DOCUMENT, "invalid JSON document") from exc

        nodes: list[StructuralNode] = []
        self._walk(artifact, value, nodes, pointer="")
        return StructuralDocument(
            artifact=parser_artifact(artifact, name=self.name, version=self.version),
            nodes=tuple(nodes),
            extraction_coverage=coverage(nodes),
        )

    def _walk(
        self,
        artifact: ArtifactEnvelope,
        value: object,
        nodes: list[StructuralNode],
        *,
        pointer: str,
    ) -> None:
        if isinstance(value, Mapping):
            mapping = cast(Mapping[object, object], value)
            for key, child in mapping.items():
                self._walk(
                    artifact,
                    child,
                    nodes,
                    pointer=f"{pointer}/{_escape_pointer(str(key))}",
                )
            return
        if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
            sequence = cast(Sequence[object], value)
            for index, child in enumerate(sequence):
                self._walk(artifact, child, nodes, pointer=f"{pointer}/{index}")
            return
        nodes.append(
            StructuralNode(
                node_id=node_id(artifact, len(nodes), "PARAGRAPH"),
                artifact_id=artifact.artifact_id,
                kind=StructuralNodeKind.PARAGRAPH,
                ordinal=len(nodes),
                text=json.dumps(value, ensure_ascii=False, separators=(",", ":")),
                locator=SourceLocator(json_pointer=pointer or "/"),
            )
        )
