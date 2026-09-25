from decimal import Decimal
from pathlib import Path

from tests.integration.reference_helpers import reference_harness
from tests.integration.storage_coverage_helpers import value


async def test_reference_answer_resumes_calculation_without_promoting_official_values(
    tmp_path: Path,
) -> None:
    async with reference_harness(tmp_path) as h:
        request = {**h.request, "target": {**h.request["target"], "denominator": None}}
        first = value(await h.input("ask", reference_request=request))
        inquiry = first["reference_inquiry"]
        assert inquiry["state"] == "NEEDS_INPUT" and inquiry["calculation"] is None
        question = next(q for q in inquiry["questions"] if q["field"] == "denominator")
        current = await h.read()
        result = value(
            await h.input(
                "answer",
                reference_answer={
                    "criterion_id": h.criterion["criterion_id"],
                    "expected_revision_digest": current["revision_digest"],
                    "inquiry_id": inquiry["inquiry_id"],
                    "question_id": question["question_id"],
                    "value": "per-request",
                },
            )
        )
        done = result["reference_inquiry"]
        assert done["state"] == "CALCULATED", done["reason_codes"]
        assert done["inquiry_id"] == inquiry["inquiry_id"]
        assert done["answers"][-1]["provenance"] == "HUMAN_ASSERTION"
        assert done["calculation"]["calculator_version"] == "1.0.0"
        assert Decimal(done["calculation"]["lower"]) == Decimal("120")
        assert Decimal(done["calculation"]["upper"]) == Decimal("140")
        assert done["authorization_state"] == "NOT_AUTHORIZED"
        assert done["evaluator_input_allowed"] is False
        after = await h.read("read-after-answer")
        for field in (
            "acceptance_rule",
            "usage_authorization",
            "result_and_uncertainty",
            "measurement_validity",
        ):
            assert after[field] == h.criterion[field]
        assert result["reference_commit"]["committed_revision_ids"]
