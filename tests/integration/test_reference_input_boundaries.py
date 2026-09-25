from copy import deepcopy
from pathlib import Path

import pytest
from tests.integration.reference_helpers import reference_harness
from tests.integration.storage_coverage_helpers import request, value


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ("value", "REFERENCE_VALUE_GROUNDING_MISSING"),
        ("quote", "REFERENCE_FIELD_GROUNDING_MISSING"),
        ("foreign_field", "REFERENCE_FIELD_GROUNDING_MISSING"),
        ("context", "REFERENCE_CONTEXT_INCOMPATIBLE"),
        ("unit", "REFERENCE_UNIT_INCOMPATIBLE"),
        ("duplicate", "REFERENCE_INDEPENDENT_SOURCES_REQUIRED"),
        ("calculator", "REFERENCE_CALCULATOR_UNAVAILABLE"),
    ],
)
async def test_normal_reference_input_holds_invalid_calculation(
    tmp_path: Path, change: str, reason: str
) -> None:
    async with reference_harness(tmp_path) as h:
        body = deepcopy(h.request)
        first = body["measurements"][0]
        if change == "value":
            first["value"] = "999"
        elif change == "quote":
            first["quotes"]["denominator"] = "invented denominator"
        elif change == "foreign_field":
            first["field_span_ids"]["denominator"] = body["measurements"][1]["field_span_ids"][
                "denominator"
            ]
        elif change == "context":
            body["target"]["denominator"] = "another population"
        elif change == "unit":
            body["target"]["unit"] = "kg"
        elif change == "duplicate":
            body["measurements"] = [first, deepcopy(first)]
        elif change == "calculator":
            body["calculator_version"] = "2.0.0"
        result = value(await h.input("invalid-reference", reference_request=body))
        inquiry = result["reference_inquiry"]
        assert inquiry["state"] == "HOLD"
        assert inquiry["reason_codes"] == [reason]
        assert inquiry["calculation"] is None
        current = await h.read()
        for field in ("acceptance_rule", "usage_authorization", "result_and_uncertainty"):
            assert current[field] == h.criterion[field]


async def test_reference_annotation_cannot_be_patched_through_public_correction(
    tmp_path: Path,
) -> None:
    async with reference_harness(tmp_path) as h:
        result = await h.runtime.bus.dispatch(
            request(
                "criteria/field/correct",
                "forge-inquiry",
                {
                    "project_id": h.project,
                    "criterion_id": h.criterion["criterion_id"],
                    "expected_revision_digest": h.criterion["revision_digest"],
                    "field_path": "reference_inquiry",
                    "proposed_value": {"state": "CALCULATED"},
                    "evidence_span_ids": h.request["source_refs"],
                    "reason": "Untrusted annotation attempt",
                },
            )
        )
        assert result.error is not None
        assert "REFERENCE_PRODUCER_REQUIRED" in str(result.error)
        assert (await h.read())["revision_digest"] == h.criterion["revision_digest"]


async def test_unapproved_formula_is_rejected_before_normal_research(tmp_path: Path) -> None:
    async with reference_harness(tmp_path) as h:
        result = await h.input(
            "unapproved",
            reference_request={
                **h.request,
                "lane": "RECALCULATED_VALUE",
                "calculator_id": "approved-formula",
            },
        )
        assert result.error is not None
        assert "REFERENCE_FORMULA_NOT_AUTHORIZED" in str(result.error)
        assert (await h.read())["revision_digest"] == h.criterion["revision_digest"]
