from __future__ import annotations

import json
from typing import Any

from thoth.adapters.parsers.registry import default_parser_registry
from thoth.domain.enums import ParserErrorCode, StructuralNodeKind


def test_git_manifest_preserves_blob_identity_path_and_dirty_state(artifact_factory: Any) -> None:
    raw = json.dumps(
        {
            "repository": "fixture",
            "head": "a" * 40,
            "dirty": True,
            "entries": [
                {
                    "path": "src/main.py",
                    "mode": "100644",
                    "object_id": "b" * 40,
                    "stage": 0,
                    "status": "M",
                }
            ],
        }
    ).encode()
    artifact = artifact_factory(
        raw, "application/vnd.thoth.git-manifest+json", ".git-manifest.json"
    )

    document = default_parser_registry().parse(artifact, raw)

    assert document.artifact.parser_name == "git"
    assert document.nodes[0].kind == StructuralNodeKind.PARAGRAPH
    assert document.nodes[0].locator.file_path == "src/main.py"
    assert "object=" + "b" * 40 in (document.nodes[0].text or "")
    assert document.warnings[0].code == ParserErrorCode.DIRTY_GIT_TARGET
