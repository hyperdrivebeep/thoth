"""Read-only v5/older result projections; legacy citations never acquire support authority."""

from pydantic import JsonValue, ValidationError

from thoth.domain.prediction_grounding import GroundingSourceBinding

_SERVER_FIELDS = {
    "grounding",
    "source_stage",
    "source_bindings",
    "relation_assessment",
    "binding_availability",
    "target",
    "grounding_projection",
    "source_contract",
}


def result_rows(result: dict[str, JsonValue]) -> list[dict[str, JsonValue]]:
    portfolio = result.get("portfolio")
    owners = portfolio.get("hypotheses") if isinstance(portfolio, dict) else None
    rows: list[dict[str, JsonValue]] = []
    for owner in owners if isinstance(owners, list) else ():
        if not isinstance(owner, dict):
            continue
        tests = owner.get("discriminating_tests")
        for test in tests if isinstance(tests, list) else ():
            table = test.get("expected_by_hypothesis") if isinstance(test, dict) else None
            if isinstance(table, list):
                rows.extend(item for item in table if isinstance(item, dict))
    return rows


def has_grounding_contract(result: dict[str, JsonValue]) -> bool:
    return result.get("hypothesis_contract_version") == "hypothesis_portfolio.v5" or any(
        "grounding" in row for row in result_rows(result)
    )


def project_grounding_rows(
    result: dict[str, JsonValue],
    requested: str | None,
    source_basis: JsonValue = None,
    source_refs: JsonValue = None,
) -> bool:
    changed = False
    for row in result_rows(result):
        if requested != "v5":
            for key in _SERVER_FIELDS.intersection(row):
                del row[key]
                changed = True
            continue
        if "grounding" in row:
            continue
        original = "v4" if "knowledge_state" in row else "v3"
        if "knowledge_state" not in row:
            unknown = " ".join(str(row.get("expected", "")).split()) == "모름"
            row["knowledge_state"] = "UNKNOWN" if unknown else "PREDICTED"
            if unknown:
                row["expected"] = None
        row["grounding"] = {
            "schema_version": "1.0.0",
            "derivation_role": "UNASSESSED",
            "rationale": "",
        }
        row["relation_assessment"] = {
            "schema_version": "1.0.0",
            "assessment_status": "UNASSESSED",
            "reason": "SEMANTIC_ASSESSMENT_NOT_PERFORMED",
        }
        bindings: list[JsonValue] = []
        ids = row.get("basis")
        available = isinstance(ids, list)
        for identifier in ids if isinstance(ids, list) else ():
            if (
                not isinstance(identifier, str)
                or not isinstance(source_refs, list)
                or identifier not in source_refs
            ):
                available = False
                break
            value = source_basis.get(str(identifier)) if isinstance(source_basis, dict) else None
            version, separator, digest = (
                value.rpartition(":") if isinstance(value, str) else ("", "", "")
            )
            if not separator:
                available = False
                break
            try:
                binding = GroundingSourceBinding(
                    span_id=identifier, source_version_id=version, text_sha256=digest
                )
            except ValidationError:
                available = False
                break
            bindings.append(binding.model_dump(mode="json"))
        row["source_bindings"] = bindings if available else []
        row["binding_availability"] = "AVAILABLE" if available else "UNAVAILABLE"
        row["source_contract"] = original
        row["grounding_projection"] = "LEGACY_UNASSESSED"
        changed = True
    return changed
