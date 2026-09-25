from decimal import Decimal
from pathlib import Path

from tests.integration.reference_helpers import reference_harness
from tests.integration.storage_coverage_helpers import value


async def test_cited_source_selection_scenarios_compute_sensitivity_and_hold_invalid_subset(
    tmp_path: Path,
) -> None:
    async with reference_harness(
        tmp_path,
        observations=(
            ("first.md", "120", "ms", None),
            ("second.md", "0.14", "s", None),
            ("third.md", "160", "ms", None),
        ),
    ) as h:
        refs = [m["span_id"] for m in h.request["measurements"]]
        result = value(
            await h.input(
                "scenarios",
                reference_request={
                    **h.request,
                    "scenarios": [
                        {
                            "scenario_id": "first-two",
                            "label": "First two cited observations",
                            "measurement_span_ids": refs[:2],
                        },
                        {
                            "scenario_id": "last-two",
                            "label": "Last two cited observations",
                            "measurement_span_ids": refs[1:],
                        },
                        {
                            "scenario_id": "single",
                            "label": "Insufficient independent evidence",
                            "measurement_span_ids": refs[:1],
                        },
                    ],
                },
            )
        )
        inquiry = result["reference_inquiry"]
        assert inquiry["state"] == "CALCULATED"
        first, last, held = inquiry["scenario_results"]
        assert Decimal(first["calculation"]["upper"]) == Decimal("140")
        assert Decimal(first["upper_delta_from_base"]) == Decimal("-20")
        assert Decimal(last["lower_delta_from_base"]) == Decimal("20")
        assert held["state"] == "HOLD" and held["calculation"] is None
        assert held["reason_codes"] == ["REFERENCE_INDEPENDENT_SOURCES_REQUIRED"]
        expected_refs = {
            ref
            for m in h.request["measurements"][:2]
            for ref in (m["span_id"], *m["field_span_ids"].values())
        }
        assert set(first["calculation"]["source_refs"]) == expected_refs
        assert inquiry["evaluator_input_allowed"] is False
