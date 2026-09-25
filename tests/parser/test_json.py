from __future__ import annotations

from typing import Any

import pytest

from thoth.adapters.parsers.registry import default_parser_registry
from thoth.domain.enums import ParserErrorCode
from thoth.domain.errors import ParserFailure


def test_json_uses_json_pointer_and_original_byte_hash(artifact_factory: Any) -> None:
    raw = '{"metric":{"name":"정확도","targets":[94,95]}}'.encode()
    artifact = artifact_factory(raw, "application/json", ".json")

    document = default_parser_registry().parse(artifact, raw)

    pointers = {node.locator.json_pointer: node.text for node in document.nodes}
    assert pointers["/metric/name"] == '"정확도"'
    assert pointers["/metric/targets/1"] == "95"
    assert document.artifact.byte_sha256 == artifact.byte_sha256


def test_json_rejects_byte_hash_mismatch(artifact_factory: Any) -> None:
    raw = b'{"ok":true}'
    artifact = artifact_factory(raw, "application/json", ".json").model_copy(
        update={"byte_sha256": "0" * 64}
    )

    with pytest.raises(ParserFailure) as captured:
        default_parser_registry().parse(artifact, raw)

    assert captured.value.code == ParserErrorCode.BYTE_HASH_MISMATCH
