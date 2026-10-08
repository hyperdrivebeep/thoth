"""Hypothesis request schemas and timezone/validity/default contracts before model extraction."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import cast

import pytest
from pydantic import BaseModel, ValidationError

from thoth.application.commands import hypotheses_full

FIXTURE = Path(__file__).parents[2] / "fixtures/hypothesis_input_schemas.json"
SCHEMAS = cast(dict[str, object], json.loads(FIXTURE.read_text(encoding="utf-8")))


def input_model(name: str) -> type[BaseModel]:
    owner = importlib.import_module(hypotheses_full.HypothesisListInput.__module__)
    model = getattr(owner, name)
    assert isinstance(model, type) and issubclass(model, BaseModel)
    return model


@pytest.mark.parametrize("name", tuple(SCHEMAS))
def test_input_schema_matches_original(name: str) -> None:
    assert input_model(name).model_json_schema() == SCHEMAS[name]


def test_list_defaults_length_extra_and_frozen_contract() -> None:
    value = hypotheses_full.HypothesisListInput(project_id="p")
    assert value.model_dump(mode="json") == {
        "project_id": "p",
        "object_id": None,
        "portfolio_id": None,
        "primary_intent": None,
        "stage": None,
        "appraisal": None,
        "freshness": None,
    }
    assert hypotheses_full.HypothesisListInput(project_id="x" * 160).project_id == "x" * 160
    for invalid in ("", "x" * 161):
        with pytest.raises(ValidationError):
            hypotheses_full.HypothesisListInput(project_id=invalid)
    with pytest.raises(ValidationError) as extra:
        hypotheses_full.HypothesisListInput.model_validate({"project_id": "p", "extra": True})
    assert extra.value.errors()[0]["type"] == "extra_forbidden"
    with pytest.raises(ValidationError) as frozen:
        value.project_id = "q"
    assert frozen.value.errors()[0]["type"] == "frozen_instance"


def test_generation_and_creation_defaults_preserve_order_and_unknown_state() -> None:
    generated = (
        input_model("GenerateInput")
        .model_validate(
            {
                "project_id": "p",
                "object_id": "object:1",
                "question": "Question",
                "evidence_scope": ["span:z", "span:a"],
                "generation_policy_ref": "policy:1",
            }
        )
        .model_dump(mode="json")
    )
    assert generated["evidence_scope"] == ["span:z", "span:a"]
    assert generated["intent_hints"] == [] and generated["budget_policy_ref"] is None
    created = (
        input_model("CreateInput")
        .model_validate(
            {
                "project_id": "p",
                "object_id": "object:1",
                "portfolio_id": "portfolio:1",
                "statement": "Statement",
                "primary_intent": "EXPLANATION",
                "evidence_basis": "Basis",
                "scope": {},
                "evidence_refs": [],
            }
        )
        .model_dump(mode="json")
    )
    assert created["prespecification_state"] == "UNKNOWN"
    assert created["secondary_intents"] == [] and created["expected_object_revision"] is None


def test_prediction_cutoff_requires_timezone_and_preserves_offset() -> None:
    model = input_model("PredictionBindInput")
    payload: dict[str, object] = {
        "project_id": "p",
        "hypothesis_id": "hypothesis:1",
        "hypothesis_revision_digest": "z" * 64,
        "knowledge_cutoff": "2026-10-08T09:00:00+09:00",
        "prespecification_state": "UNKNOWN",
        "conditions": {},
        "measurement_contract_ref": "measurement:1",
        "assumption_refs": [],
        "expected_outcome": {},
        "discrimination_map": {},
    }
    assert model.model_validate(payload).model_dump(mode="json")["knowledge_cutoff"] == (
        "2026-10-08T09:00:00+09:00"
    )
    with pytest.raises(ValidationError) as naive:
        model.model_validate({**payload, "knowledge_cutoff": "2026-10-08T09:00:00"})
    assert naive.value.errors()[0]["type"] == "timezone_aware"
    with pytest.raises(ValidationError):
        model.model_validate({**payload, "hypothesis_revision_digest": "z" * 63})


def test_test_binding_defaults_and_both_patterns() -> None:
    model = input_model("TestBindInput")
    payload: dict[str, object] = {
        "project_id": "p",
        "prediction_id": "prediction:1",
        "execution_ref": "execution:1",
        "observation_refs": [],
        "test_validity_assessment_ref": "assessment:1",
    }
    result = model.model_validate(payload).model_dump(mode="json")
    assert result["test_validity"] == "NOT_ASSESSABLE"
    assert result["prediction_fit"] == "INCONCLUSIVE"
    assert model.model_validate({**payload, "test_validity": "VALID", "prediction_fit": "MATCH"})
    for field, invalid in (("test_validity", "valid"), ("prediction_fit", "MATCHED")):
        with pytest.raises(ValidationError) as pattern:
            model.model_validate({**payload, field: invalid})
        assert pattern.value.errors()[0]["type"] == "string_pattern_mismatch"


def test_portfolio_and_merge_minimums_keep_original_input_contract() -> None:
    model = input_model("PortfolioComposeInput")
    base: dict[str, object] = {"project_id": "p", "object_id": "object:1", "unknown_reserve": {}}
    with pytest.raises(ValidationError):
        model.model_validate({**base, "hypothesis_ids": ["one"]})
    result = model.model_validate({**base, "hypothesis_ids": ["z", "a"]}).model_dump(mode="json")
    assert result["hypothesis_ids"] == ["z", "a"] and result["relation_candidates"] == []
    assert result["expected_object_revision"] is None and result["portfolio_id"] is None
    with pytest.raises(ValidationError):
        input_model("MergeProposeInput").model_validate(
            {
                "project_id": "p",
                "hypothesis_ids": ["a", "b"],
                "field_mapping": {},
                "evidence_refs": [],
                "rationale": "Reason",
                "expected_revision_digests": ["a" * 64],
            }
        )
