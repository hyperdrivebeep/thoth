from __future__ import annotations

from typing import cast

import pytest

from thoth.adapters.models.reference_schema import (
    constrain_hypothesis_review,
    constrain_span_references,
)


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    assert all(isinstance(key, str) for key in cast(dict[object, object], value))
    return cast(dict[str, object], value)


@pytest.mark.parametrize("ids", [(), ("hyp:a",), ("hyp:a", "hyp:b")])
def test_hypothesis_schema_pins_exact_ids_in_place(ids: tuple[str, ...]) -> None:
    schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "decisions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"hypothesis_id": {"type": "string", "enum": ["stale"]}},
                },
            }
        },
    }
    assert constrain_hypothesis_review(schema, ids) is schema
    decisions = _mapping(_mapping(schema["properties"])["decisions"])
    assert (decisions["minItems"], decisions["maxItems"]) == (len(ids), len(ids))
    hypothesis = _mapping(_mapping(_mapping(decisions["items"])["properties"])["hypothesis_id"])
    if ids:
        assert hypothesis["enum"] == list(ids)
    else:
        assert "enum" not in hypothesis


def test_span_and_requirement_arrays_keep_exact_supplied_ids() -> None:
    schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "evidence_refs": {"type": "array", "items": {"type": "string", "enum": ["stale"]}},
            "requirement_ids": {"type": "array", "items": {"type": "string"}},
        },
    }
    context: dict[str, object] = {
        "requirements": {"requirements": [{"requirement_id": "req:a"}, {"ignored": "x"}]}
    }
    assert constrain_span_references(schema, ("span:a",), context) is schema
    fields = _mapping(schema["properties"])
    evidence = _mapping(fields["evidence_refs"])
    requirements = _mapping(fields["requirement_ids"])
    assert _mapping(evidence["items"])["enum"] == ["span:a"]
    assert _mapping(requirements["items"])["enum"] == ["req:a"]
    constrain_span_references(schema, (), context)
    assert _mapping(evidence["items"]).get("enum") is None
    assert evidence["maxItems"] == 0
