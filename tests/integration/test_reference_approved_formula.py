from decimal import Decimal
from pathlib import Path

from tests.integration.reference_helpers import TARGET, reference_harness
from tests.integration.storage_coverage_helpers import request, value


async def test_grounded_approved_formula_recalculation_keeps_official_target(
    tmp_path: Path,
) -> None:
    target = {
        **TARGET,
        "metric_definition": "sample ratio",
        "formula": "n / d * 100",
        "unit": "%",
        "denominator": "cohort count",
        "measurement_method": "count observation",
    }
    async with reference_harness(
        tmp_path,
        target=target,
        observations=(
            ("numerator.md", "12", "count", "n"),
            ("denominator.md", "200", "count", "d"),
        ),
    ) as h:
        assert h.criterion["computation_spec"] is None
        binding = "b" * 64  # Fixture's declared evaluator binding, not a live approval.
        corrected = value(
            await h.runtime.bus.dispatch(
                request(
                    "criteria/field/correct",
                    "bind-formula",
                    {
                        "project_id": h.project,
                        "criterion_id": h.criterion["criterion_id"],
                        "expected_revision_digest": h.criterion["revision_digest"],
                        "field_path": "computation_spec",
                        "proposed_value": {
                            "type": "FORMULA",
                            "expression": target["formula"],
                            "unit": "%",
                            "input_units": {"n": "count", "d": "count"},
                            "evaluator_binding_digest": binding,
                        },
                        "evidence_span_ids": h.request["source_refs"],
                        "reason": "Bind the declared synthetic source formula",
                    },
                )
            )
        )["criterion"]
        approved = value(
            await h.runtime.bus.dispatch(
                request(
                    "criteria/revalidate",
                    "ready-formula",
                    {
                        "project_id": h.project,
                        "criterion_id": h.criterion["criterion_id"],
                        "revision_digest": corrected["revision_digest"],
                        "trigger_reason": "source fields bound",
                    },
                )
            )
        )["criterion"]
        assert approved["usage_authorization"] == "AUTHORIZED_EVALUATOR_INPUT"
        result = value(
            await h.input(
                "calculate-formula",
                reference_request={
                    **h.request,
                    "expected_revision_digest": approved["revision_digest"],
                    "lane": "RECALCULATED_VALUE",
                    "calculator_id": "approved-formula",
                    "evaluator_binding_digest": binding,
                },
            )
        )
        inquiry = result["reference_inquiry"]
        assert inquiry["state"] == "CALCULATED", inquiry["reason_codes"]
        assert inquiry["lane"] == "RECALCULATED_VALUE"
        assert Decimal(inquiry["calculation"]["value"]) == Decimal("6")
        current = await h.read()
        assert Decimal(current["result_and_uncertainty"]["value"]) == Decimal("6")
        assert current["result_and_uncertainty"]["measurement_validity"] == "NOT_ASSESSED"
        assert current["result_and_uncertainty"]["disposition"] == "NOT_DECIDED"
        assert current["acceptance_rule"] == approved["acceptance_rule"]
        assert current["usage_authorization"] == approved["usage_authorization"]
        assert current["reference_inquiry"]["evaluator_input_allowed"] is False
