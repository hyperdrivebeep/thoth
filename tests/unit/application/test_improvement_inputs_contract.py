"""Freeze improvement input shape while leaving target policy and approval with handlers."""

import importlib
import json
from pathlib import Path
from typing import cast

import pytest
from pydantic import BaseModel, ValidationError

from thoth.application.commands import improvement_full as owner

SCHEMAS = cast(
    dict[str, object],
    json.loads(
        (Path(__file__).parents[2] / "fixtures/improvement_inputs_schemas.json").read_bytes()
    ),
)


@pytest.mark.parametrize("name", tuple(SCHEMAS))
def test_original_schema_and_legacy_identity(name: str) -> None:
    module = importlib.import_module(owner.ListInput.__module__)
    model = getattr(module, name)
    assert isinstance(model, type) and issubclass(model, BaseModel)
    assert getattr(owner, name) is model
    assert model.model_json_schema() == SCHEMAS[name]


def test_defaults_extra_frozen_and_digest_lengths() -> None:
    item = owner.ListInput(project_id="p")
    assert item.model_dump() == {
        "project_id": "p",
        **dict.fromkeys(
            ("target_component", "candidate_lifecycle", "exposure_state", "promotion_state")
        ),
    }
    assert (
        owner.ImprovementReadInput(project_id="p", improvement_revision_id="i").revision_digest
        is None
    )
    with pytest.raises(ValidationError) as extra:
        owner.ListInput.model_validate({"project_id": "p", "extra": True})
    assert extra.value.errors()[0]["type"] == "extra_forbidden"
    with pytest.raises(ValidationError) as frozen:
        item.project_id = "q"
    assert frozen.value.errors()[0]["type"] == "frozen_instance"
    for length in (63, 65):
        with pytest.raises(ValidationError):
            owner.ImprovementReadInput(
                project_id="p", improvement_revision_id="i", revision_digest="z" * length
            )


def test_proposal_json_order_nullable_thread_and_text_limits() -> None:
    data: dict[str, object] = {
        "project_id": "p",
        "target_component": "PROMPT",
        "scope_key": "scope",
        "trigger_refs": ["b", "a", "b"],
        "baseline_digest": "z" * 64,
        "candidate_content": {"enabled": False, "nested": [None, 1]},
        "improvement_hypothesis": "h" * 5000,
        "evaluation_contract_ref": "contract",
    }
    item = owner.ProposeInput.model_validate(data)
    assert item.thread_id is None and item.trigger_refs == ("b", "a", "b")
    assert item.model_dump(mode="json")["candidate_content"] == data["candidate_content"]
    for patch in (
        {"thread_id": ""},
        {"improvement_hypothesis": "h" * 5001},
        {"baseline_digest": "short"},
    ):
        with pytest.raises(ValidationError):
            owner.ProposeInput.model_validate({**data, **patch})


@pytest.mark.parametrize("state", ("OFFLINE", "SANDBOX", "SHADOW", "CANARY"))
def test_exposure_vocabulary_preserved(state: str) -> None:
    data: dict[str, object] = {
        "project_id": "p",
        "improvement_revision_id": "i",
        "requested_exposure_state": state,
        "evaluation_plan_id": "e",
        "scope": {},
        "duration_budget": {"steps": 0},
        "stop_rollback_contract": {},
    }
    assert owner.ExposurePrepareInput.model_validate(data).requested_exposure_state == state
    with pytest.raises(ValidationError):
        owner.ExposurePrepareInput.model_validate(
            {**data, "requested_exposure_state": "PRODUCTION"}
        )


@pytest.mark.parametrize("decision", ("APPROVE", "REJECT"))
def test_approval_and_assessment_revision_bounds(decision: str) -> None:
    data: dict[str, object] = {
        "project_id": "p",
        "promotion_candidate_id": "c",
        "decision": decision,
        "actor_ref": "human",
        "role_assignment_ref": "r",
        "approved_digest": "z" * 64,
    }
    assert owner.PromotionDecideInput.model_validate(data).reason is None
    with pytest.raises(ValidationError):
        owner.PromotionDecideInput.model_validate({**data, "decision": decision.lower()})
    assessment: dict[str, object] = {
        "project_id": "p",
        "evaluation_plan_id": "e",
        "result_refs": [],
        "evaluator_result_refs": [],
        "exposure_ledger_ref": "l",
        "expected_evaluation_revision": 1,
    }
    assert owner.EvaluationAssessInput.model_validate(assessment).expected_evaluation_revision == 1
    with pytest.raises(ValidationError):
        owner.EvaluationAssessInput.model_validate(
            {**assessment, "expected_evaluation_revision": 0}
        )
