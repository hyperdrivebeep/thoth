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


def _is_number(value: str) -> bool:
    try:
        float(value.strip().replace(",", ""))
    except ValueError:
        return False
    return True


def _is_header(row: list[str]) -> bool:
    """The first row names the columns when it holds words only (no cell is a number)."""
    cells = [value.strip() for value in row if value.strip()]
    return bool(cells) and not any(_is_number(value) for value in cells)


def _row_range(row_number: int, width: int) -> str:
    first = f"R{row_number}C1"
    return first if width <= 1 else f"{first}:R{row_number}C{width}"


def _column(names: list[str] | None, index: int) -> str:
    named = names[index] if names is not None and index < len(names) else ""
    return named or f"col{index + 1}"


class CsvParser:
    """One header node and one node per data row, written as "column=value" pairs.

    A row keeps its column names, so a short row still says what its values are. The row's place is
    its row number in the file (R<row>C1:R<row>C<last>, /rows/<row>) and the line where it starts.
    A file whose first row holds a number has no header row: the columns are named col1, col2, ...
    """

    name = "csv"
    version = "1.1.0"
    media_types = frozenset({"text/csv"})
    suffixes = frozenset({".csv"})

    def parse(self, artifact: ArtifactEnvelope, raw: bytes) -> StructuralDocument:
        validate_byte_hash(artifact, raw)
        reader = csv.reader(io.StringIO(raw.decode("utf-8-sig")))
        nodes: list[StructuralNode] = []
        names: list[str] | None = None
        first = True
        end_line = 0
        for row_number, row in enumerate(reader, start=1):
            start_line, end_line = end_line + 1, reader.line_num
            if not any(value.strip() for value in row):
                continue
            header = first and _is_header(row)
            if header:
                names = [value.strip() for value in row]
            first = False
            kind = StructuralNodeKind.HEADER if header else StructuralNodeKind.ROW
            text = (
                ", ".join(value for value in names or () if value)
                if header
                else ", ".join(
                    f"{_column(names, index)}={value.strip()}"
                    for index, value in enumerate(row)
                    if value.strip()
                )
            )
            nodes.append(
                StructuralNode(
                    node_id=node_id(artifact, len(nodes), kind.value),
                    artifact_id=artifact.artifact_id,
                    kind=kind,
                    ordinal=len(nodes),
                    text=text,
                    locator=SourceLocator(
                        line=start_line,
                        cell_range=_row_range(row_number, len(row)),
                        json_pointer="/header" if header else f"/rows/{row_number}",
                    ),
                )
            )
        return StructuralDocument(
            artifact=parser_artifact(artifact, name=self.name, version=self.version),
            nodes=tuple(nodes),
            extraction_coverage=coverage(nodes),
        )
