"""The effort-estimate contract is the same in the model schema, prompt and consumer."""

import json
from typing import cast

from thoth.adapters.models.codex_oauth import role_contract, strict_output_schema
from thoth.domain.action import ActionPlanDraft
from thoth.domain.effort_bands import EFFORT_BAND_PROFILE_V1


def _draft_action_schema() -> dict[str, object]:
    schema = strict_output_schema(ActionPlanDraft)
    text = json.dumps(schema)
    assert "effort_estimates" in text
    definitions = cast(dict[str, dict[str, object]], schema["$defs"])
    return definitions["ActionDraft"]


def test_model_output_schema_asks_only_for_model_authored_effort_fields() -> None:
    draft = _draft_action_schema()
    assert "effort_estimates" in draft["required"]  # type: ignore[operator]
    entry = json.dumps(strict_output_schema(ActionPlanDraft)["$defs"]["EffortEstimateDraft"])  # type: ignore[index]
    for field in ("dimension", "band", "basis_text", "assumptions"):
        assert field in entry
    for server_field in ("estimator_type", "estimator_ref", "profile_id", "created_at"):
        assert server_field not in entry


def test_prompt_for_the_action_planner_carries_the_band_table_and_basis_rule() -> None:
    contract = role_contract("ACTION_PLANNER")
    assert "effort_estimates" in contract
    assert EFFORT_BAND_PROFILE_V1.profile_id in contract
    assert "UNKNOWN" in contract and "basis_text" in contract
    for definition in EFFORT_BAND_PROFILE_V1.definitions:
        assert definition.criterion in contract
