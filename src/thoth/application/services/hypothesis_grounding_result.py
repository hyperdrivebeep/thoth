"""Result DTO metadata is derived from the same accepted rows and immutable record refs."""

from copy import deepcopy
from typing import cast

from pydantic import JsonValue

from thoth.application.services.hypothesis_grounding_bindings import bind_grounded_row
from thoth.application.services.hypothesis_grounding_hold import GroundingPublicationHold
from thoth.domain.hypothesis_v5 import ExpectedResultV5
from thoth.domain.research_execution import ResearchWork


def grounded_result_payload(
    payload: dict[str, JsonValue],
    work: ResearchWork | None,
) -> dict[str, JsonValue]:
    if work is None or work.hypothesis_grounding is None:
        return payload
    shown = deepcopy(payload)
    portfolio = shown.get("portfolio")
    if not isinstance(portfolio, dict) or not isinstance(portfolio.get("hypotheses"), list):
        return payload
    for owner in cast(list[dict[str, JsonValue]], portfolio["hypotheses"]):
        owner_id = str(owner["hypothesis_id"])
        refs = [
            ref
            for ref in work.produced_refs
            if ref.entity_type == "HYPOTHESIS"
            and ref.entity_id == owner_id
            and ref.revision_digest == work.produced_final_heads.get(f"HYPOTHESIS:{owner_id}")
        ]
        if len(refs) != 1:
            raise GroundingPublicationHold("GROUNDING_TARGET_RECORD_UNAVAILABLE")
        for test in cast(list[dict[str, JsonValue]], owner.get("discriminating_tests", [])):
            test_id = str(test["test_id"])
            for row in cast(list[dict[str, JsonValue]], test.get("expected_by_hypothesis", [])):
                bound = bind_grounded_row(
                    owner_id, test_id, ExpectedResultV5.model_validate(row), work
                )
                row.update(bound.model_dump(mode="json"))
                row["binding_availability"] = "AVAILABLE"
                row["target"] = {
                    "owner_ref": refs[0].model_dump(mode="json"),
                    "test_id": test_id,
                    "hypothesis_id": bound.hypothesis_id,
                }
    shown["hypothesis_contract_version"] = "hypothesis_portfolio.v5"
    shown["hypothesis_grounding_diagnostics"] = [
        item.model_dump(mode="json") for item in work.hypothesis_grounding.diagnostics
    ]
    return shown
