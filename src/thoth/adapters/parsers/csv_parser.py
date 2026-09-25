from __future__ import annotations

import csv
import io

from thoth.adapters.parsers.common import coverage, node_id, parser_artifact, validate_byte_hash
from thoth.domain.artifact import (
    ArtifactEnvelope,
    SourceLocator,
    StructuralDocument,
    StructuralNode,
)
from thoth.domain.enums import StructuralNodeKind


class CsvParser:
    name = "csv"
    version = "1.0.0"
    media_types = frozenset({"text/csv"})
    suffixes = frozenset({".csv"})

    def parse(self, artifact: ArtifactEnvelope, raw: bytes) -> StructuralDocument:
        validate_byte_hash(artifact, raw)
        rows = csv.reader(io.StringIO(raw.decode("utf-8-sig")))
        nodes: list[StructuralNode] = []
        for row_number, row in enumerate(rows, start=1):
            for column_number, value in enumerate(row, start=1):
                if not value.strip():
                    continue
                cell = f"R{row_number}C{column_number}"
                nodes.append(
                    StructuralNode(
                        node_id=node_id(artifact, len(nodes), "CELL"),
                        artifact_id=artifact.artifact_id,
                        kind=StructuralNodeKind.CELL,
                        ordinal=len(nodes),
                        text=value,
                        locator=SourceLocator(cell_range=cell),
                    )
                )
        return StructuralDocument(
            artifact=parser_artifact(artifact, name=self.name, version=self.version),
            nodes=tuple(nodes),
            extraction_coverage=coverage(nodes),
        )
