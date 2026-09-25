from __future__ import annotations

from typing import Any

from thoth.adapters.parsers.registry import default_parser_registry
from thoth.domain.enums import StructuralNodeKind


def test_markdown_preserves_line_locator_and_hash(artifact_factory: Any) -> None:
    raw = "# 계획\n\n목표값은 94% 이상이다.\n".encode()
    artifact = artifact_factory(raw, "text/markdown", ".md")

    document = default_parser_registry().parse(artifact, raw)

    assert document.artifact.byte_sha256 == artifact.byte_sha256
    assert [node.kind for node in document.nodes] == [
        StructuralNodeKind.SECTION,
        StructuralNodeKind.PARAGRAPH,
    ]
    assert document.nodes[1].locator.line == 3
    assert document.extraction_coverage == "FULL"
