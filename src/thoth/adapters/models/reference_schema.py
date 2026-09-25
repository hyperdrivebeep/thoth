"""Bind the existing span-reference fields to IDs actually supplied in this call."""

from typing import cast


def constrain_span_references(
    schema: dict[str, object],
    span_ids: tuple[str, ...],
    research_context: dict[str, object] | None = None,
) -> dict[str, object]:
    context = research_context or {}
    requirements = context.get("requirements")
    ids: list[str] = []
    if isinstance(requirements, dict):
        rows = cast(dict[str, object], requirements).get("requirements")
        if isinstance(rows, list):
            ids = [
                str(cast(dict[str, object], row)["requirement_id"])
                for row in cast(list[object], rows)
                if isinstance(row, dict) and "requirement_id" in row
            ]

    def visit(value: object) -> None:
        if isinstance(value, list):
            for child in cast(list[object], value):
                visit(child)
        elif isinstance(value, dict):
            mapping = cast(dict[str, object], value)
            properties = mapping.get("properties")
            if isinstance(properties, dict):
                fields = cast(dict[str, object], properties)
                reference_fields = ["evidence_refs", "applicability_basis", "ordered_span_ids"]
                if {"conflict_id", "requirement_set_digest", "resolution"} <= fields.keys():
                    reference_fields.append("basis_refs")
                for key in reference_fields:
                    field = fields.get(key)
                    if isinstance(field, dict):
                        array = cast(dict[str, object], field)
                        if array.get("type") == "array":
                            array["items"] = (
                                {"type": "string", "enum": list(span_ids)}
                                if span_ids
                                else {"type": "string"}
                            )
                            if not span_ids:
                                array["maxItems"] = 0
                if ids:
                    for name in ("requirement_id", "requirement_ids"):
                        field = fields.get(name)
                        if isinstance(field, dict):
                            item = cast(dict[str, object], field)
                            if item.get("type") == "array":
                                item["items"] = {"type": "string", "enum": ids}
                            elif item.get("type") == "string":
                                item["enum"] = ids
            for child in tuple(mapping.values()):
                visit(child)

    visit(schema)
    return schema


def constrain_hypothesis_review(
    schema: dict[str, object], hypothesis_ids: tuple[str, ...]
) -> dict[str, object]:
    ids = list(hypothesis_ids)

    def visit(value: object) -> None:
        if isinstance(value, list):
            for child in cast(list[object], value):
                visit(child)
        elif isinstance(value, dict):
            mapping = cast(dict[str, object], value)
            properties = mapping.get("properties")
            if isinstance(properties, dict):
                fields = cast(dict[str, object], properties)
                decisions = fields.get("decisions")
                if isinstance(decisions, dict):
                    array = cast(dict[str, object], decisions)
                    if array.get("type") == "array":
                        array["minItems"] = len(ids)
                        array["maxItems"] = len(ids)
                        items = array.get("items")
                        if isinstance(items, dict):
                            item_props = cast(dict[str, object], items).get("properties")
                            if isinstance(item_props, dict):
                                hid = cast(dict[str, object], item_props).get("hypothesis_id")
                                if isinstance(hid, dict):
                                    id_field = cast(dict[str, object], hid)
                                    if ids:
                                        id_field["enum"] = ids
                                    else:
                                        id_field.pop("enum", None)
            for child in tuple(mapping.values()):
                visit(child)

    visit(schema)
    return schema


def apply_hypothesis_review_contract(
    schema: dict[str, object],
    output_model: type[object],
    research_context: dict[str, object],
) -> dict[str, object]:
    from thoth.domain.evidence_requirements import HypothesisSemanticReview

    if output_model is not HypothesisSemanticReview:
        return schema
    raw = research_context.get("hypothesis_review_target_ids")
    ids = (
        tuple(str(item) for item in cast(list[object] | tuple[object, ...], raw))
        if isinstance(raw, (list, tuple))
        else ()
    )
    return constrain_hypothesis_review(schema, ids)
