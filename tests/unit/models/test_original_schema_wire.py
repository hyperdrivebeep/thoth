"""Original JSON Schema is transported intact, including schema-looking literal data."""

import json
from copy import deepcopy

import pytest

from thoth.adapters.models.codex_http import CodexHttpExecutor
from thoth.adapters.models.codex_oauth import strict_output_schema
from thoth.adapters.models.reference_schema import constrain_span_references
from thoth.domain.evidence_requirements import ReviewProposal
from thoth.domain.model_dispatch import OAuthSession


class Session:
    def read(self) -> OAuthSession:
        return OAuthSession("unused", "unused", "selected-model", "xhigh")


def schema_cases() -> list[tuple[str, dict[str, object], object, object]]:
    literal = {"type": "string", "enum": [f"value-{i}" for i in range(40)]}
    cases: list[tuple[str, dict[str, object], object, object]] = []
    for keyword in ("const", "enum", "examples", "default"):
        field: dict[str, object] = {
            keyword: [literal] if keyword in {"enum", "examples"} else literal
        }
        cases.append(
            (
                keyword,
                {
                    "type": "object",
                    "properties": {"a": field, "b": deepcopy(field)},
                    "required": ["a", "b"],
                    "additionalProperties": False,
                },
                {"a": literal, "b": literal},
                {"a": literal},
            )
        )
    for reference in ("$ref", "$dynamicRef"):
        anchor = "$anchor" if reference == "$ref" else "$dynamicAnchor"
        child = {
            "$id": "child",
            "$defs": {"value": {anchor: "value", **literal}},
            "type": "object",
            "properties": {"a": {reference: "#value"}, "b": {reference: "#value"}},
            "required": ["a", "b"],
        }
        cases.append(
            (
                reference,
                {"$id": "https://example.invalid/root", "$defs": {"child": child}, "$ref": "child"},
                {"a": "value-0", "b": "value-39"},
                {"a": "outside", "b": "value-0"},
            )
        )
    return cases


@pytest.mark.parametrize("name,schema,valid,invalid", schema_cases())
def test_literal_and_nested_resource_schema_reaches_wire_unchanged(
    name: str, schema: dict[str, object], valid: object, invalid: object
) -> None:
    before = deepcopy(schema)
    prepared = CodexHttpExecutor(Session()).prepare(
        "Exact source context.", schema, output_tokens=6000, timeout_seconds=900
    )
    wire = json.loads(prepared.payload)
    assert wire["text"]["format"]["schema"] == before == schema, name
    assert wire["input"][0]["content"][0]["text"] == "Exact source context."
    assert wire["model"] == "selected-model" and wire["reasoning"] == {"effort": "xhigh"}
    assert prepared.timeout_seconds == 900 and prepared.max_visible_output_bytes is None


@pytest.mark.parametrize("span_count", [0, 134])
def test_constrained_review_schema_keeps_all_allowed_ids_and_empty_evidence_rule(
    span_count: int,
) -> None:
    spans = tuple(f"span:independent-{i}" for i in range(span_count))
    schema = constrain_span_references(strict_output_schema(ReviewProposal), spans)
    prepared = CodexHttpExecutor(Session()).prepare(
        "Evidence", schema, output_tokens=6000, timeout_seconds=900
    )
    actual = json.loads(prepared.payload)["text"]["format"]["schema"]
    assert actual == schema
    field = actual["$defs"]["SemanticReviewCandidate"]["properties"]["evidence_refs"]
    assert field["items"].get("enum", []) == list(spans)
    if not spans:
        assert field["maxItems"] == 0
