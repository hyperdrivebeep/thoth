from __future__ import annotations

import json
from typing import cast

from pydantic import BaseModel, ConfigDict

from thoth.adapters.parsers.common import coverage, node_id, parser_artifact, validate_byte_hash
from thoth.domain.artifact import (
    ArtifactEnvelope,
    ParseIssue,
    SourceLocator,
    StructuralDocument,
    StructuralNode,
)
from thoth.domain.enums import ParserErrorCode, StructuralNodeKind


class TestLogRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    run_id: str
    timestamp: str
    level: str
    event: str


class TestLogParser:
    name = "test-log"
    version = "1.0.0"
    media_types = frozenset({"application/vnd.thoth.test-log+jsonl"})
    suffixes = frozenset({".testlog.jsonl"})

    def parse(self, artifact: ArtifactEnvelope, raw: bytes) -> StructuralDocument:
        validate_byte_hash(artifact, raw)
        nodes: list[StructuralNode] = []
        warnings: list[ParseIssue] = []
        for line_number, line in enumerate(raw.decode("utf-8-sig").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                loaded = cast(object, json.loads(line))
                record = TestLogRecord.model_validate(loaded)
            except (json.JSONDecodeError, ValueError):
                warnings.append(
                    ParseIssue(
                        code=ParserErrorCode.UNTRUSTED_LOG_LINE,
                        message="malformed JSONL record was preserved but not interpreted",
                        locator=SourceLocator(line=line_number),
                    )
                )
                nodes.append(
                    StructuralNode(
                        node_id=node_id(artifact, len(nodes), "UNSUPPORTED_ELEMENT"),
                        artifact_id=artifact.artifact_id,
                        kind=StructuralNodeKind.UNSUPPORTED_ELEMENT,
                        ordinal=len(nodes),
                        text=line,
                        locator=SourceLocator(line=line_number),
                        extraction_warnings=("UNTRUSTED_LOG_LINE",),
                    )
                )
                continue
            nodes.append(
                StructuralNode(
                    node_id=node_id(artifact, len(nodes), "PARAGRAPH"),
                    artifact_id=artifact.artifact_id,
                    kind=StructuralNodeKind.PARAGRAPH,
                    ordinal=len(nodes),
                    text=(
                        f"run_id={record.run_id}; timestamp={record.timestamp}; "
                        f"level={record.level}; event={record.event}"
                    ),
                    locator=SourceLocator(line=line_number, section=f"run:{record.run_id}"),
                )
            )
        return StructuralDocument(
            artifact=parser_artifact(artifact, name=self.name, version=self.version),
            nodes=tuple(nodes),
            extraction_coverage=coverage(nodes),
            warnings=tuple(warnings),
        )
