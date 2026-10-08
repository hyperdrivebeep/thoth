"""Freeze the actual action input schemas and representative validation boundaries."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import cast

import pytest
from pydantic import BaseModel, ValidationError

from thoth.application.commands import actions_full

FIXTURE = Path(__file__).parents[2] / "fixtures/actions_input_schemas.json"
SCHEMAS = cast(dict[str, object], json.loads(FIXTURE.read_text(encoding="utf-8")))


def input_model(name: str) -> type[BaseModel]:
    # Follow a real handler dependency to its defining module, rather than demanding unused
    # compatibility imports for every base class in actions_full.
    owner = importlib.import_module(actions_full.ActionListInput.__module__)
    model = getattr(owner, name)
    assert isinstance(model, type) and issubclass(model, BaseModel)
    return model


@pytest.mark.parametrize("name", tuple(SCHEMAS))
def test_input_schema_matches_original(name: str) -> None:
    assert input_model(name).model_json_schema() == SCHEMAS[name]


def test_list_defaults_project_bounds_extra_fields_and_frozen_input() -> None:
    value = actions_full.ActionListInput(project_id="p")
    assert value.model_dump(mode="json") == {
        "project_id": "p",
        "object_id": None,
        "portfolio_id": None,
        "purpose": None,
        "proposal_state": None,
        "policy_state": None,
        "authorization_state": None,
    }
    assert actions_full.ActionListInput(project_id="x" * 160).project_id == "x" * 160
    for project in ("", "x" * 161):
        with pytest.raises(ValidationError):
            actions_full.ActionListInput(project_id=project)
    with pytest.raises(ValidationError) as extra:
        actions_full.ActionListInput.model_validate({"project_id": "p", "unexpected": True})
    assert extra.value.errors()[0]["type"] == "extra_forbidden"
    with pytest.raises(ValidationError) as frozen:
        value.project_id = "changed"
    assert frozen.value.errors()[0]["type"] == "frozen_instance"


def test_revision_defaults_and_nested_human_estimate_validation() -> None:
    model = input_model("ReviseInput")
    payload: dict[str, object] = {
        "project_id": "p",
        "action_id": "action:1",
        "expected_revision_digest": "a" * 64,
        "evidence_refs": ["span:1"],
        "reason": "Reason",
    }
    dumped = model.model_validate(payload).model_dump(mode="json")
    assert dumped["patch"] == {} and dumped["human_effort_estimates"] == []
    assert dumped["estimator_ref"] is None and dumped["evidence_refs"] == ["span:1"]
    estimate = {"dimension": "TIME", "band": "LOW"}
    revised = model.model_validate({**payload, "human_effort_estimates": [estimate]})
    assert revised.model_dump(mode="json")["human_effort_estimates"] == [
        {"dimension": "TIME", "band": "LOW", "basis_text": "", "assumptions": []}
    ]
    with pytest.raises(ValidationError):
        model.model_validate({**payload, "human_effort_estimates": [{**estimate, "band": "LOWER"}]})


def test_portfolio_minimum_and_empty_plan_input_defaults() -> None:
    payload = {"project_id": "p", "object_id": "object:1", "decision_need": "Decide"}
    model = input_model("PortfolioComposeInput")
    with pytest.raises(ValidationError) as too_short:
        model.model_validate({**payload, "action_ids": ["a"]})
    assert too_short.value.errors()[0]["type"] == "too_short"
    accepted = model.model_validate({**payload, "action_ids": ["b", "a"]}).model_dump(mode="json")
    assert accepted["action_ids"] == ["b", "a"] and accepted["criteria_proposal"] == []
    plan = (
        input_model("PlanComposeInput")
        .model_validate(
            {
                "project_id": "p",
                "object_id": "object:1",
                "selected_action_refs": [],
                "step_candidates": [],
                "dependency_edges": [],
            }
        )
        .model_dump(mode="json")
    )
    assert plan["selected_action_refs"] == [] and plan["expected_object_revision"] is None


@pytest.mark.parametrize("decision", ["APPROVE", "REJECT"])
def test_authorization_decision_pattern_and_digest_length(decision: str) -> None:
    model = input_model("AuthorizationDecideInput")
    payload: dict[str, object] = {
        "project_id": "p",
        "authorization_id": "authorization:1",
        "decision": decision,
        "actor_ref": "human:1",
        "role_assignment_ref": "role:1",
        "approved_digest": "z" * 64,
    }
    result = model.model_validate(payload).model_dump(mode="json")
    assert result["decision"] == decision and result["reason"] is None and result["dissent"] is None
    with pytest.raises(ValidationError) as pattern:
        model.model_validate({**payload, "decision": decision.lower()})
    assert pattern.value.errors()[0]["type"] == "string_pattern_mismatch"
    for digest in ("z" * 63, "z" * 65):
        with pytest.raises(ValidationError):
            model.model_validate({**payload, "approved_digest": digest})
