"""Final prediction invariants and pure ambiguity checks do not grant evidence authority."""

from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError
from tests.fixtures.hypothesis_grounding_v5 import prediction, stored_prediction
from tests.unit.application.test_hypothesis_table_filler import portfolio

from thoth.domain.hypothesis_v4 import expected_is_unknown
from thoth.domain.hypothesis_v5 import (
    ExpectedResultV5,
    HypothesisPortfolioV5,
    StoredExpectedResultV5,
)
from thoth.domain.hypothesis_v5_identity import prediction_identity_issues
from thoth.domain.prediction_grounding import RelationAssessmentV1


@pytest.mark.parametrize(
    "role", ["OBSERVATION_RESTATEMENT", "EVIDENCE_INFERENCE", "HYPOTHESIS_ONLY", "UNASSESSED"]
)
def test_roles_are_proposals_and_assessment_stays_unassessed(role: str) -> None:
    row = StoredExpectedResultV5.model_validate(stored_prediction(role))
    assert row.grounding.derivation_role == role
    assert row.relation_assessment.assessment_status == "UNASSESSED"
    assert "source_bindings" not in ExpectedResultV5.model_json_schema()["properties"]
    assert "relation_assessment" not in ExpectedResultV5.model_json_schema()["properties"]


@pytest.mark.parametrize("role", ["OBSERVATION_RESTATEMENT", "EVIDENCE_INFERENCE"])
def test_observation_and_inference_need_explanation_and_basis(role: str) -> None:
    value = prediction(role)
    value["grounding"]["rationale"] = "  "
    with pytest.raises(ValidationError, match="GROUNDING_RATIONALE_REQUIRED"):
        ExpectedResultV5.model_validate(value)
    value = prediction(role)
    value["basis"] = []
    with pytest.raises(ValidationError, match="GROUNDING_ROLE_BASIS_CONFLICT"):
        ExpectedResultV5.model_validate(value)


@pytest.mark.parametrize("role", ["HYPOTHESIS_ONLY", "UNASSESSED"])
def test_unconfirmed_roles_allow_empty_explanation_but_not_support_links(role: str) -> None:
    value = prediction(role)
    assert ExpectedResultV5.model_validate(value).grounding.rationale == ""
    value["basis"] = ["span:0"]
    with pytest.raises(ValidationError, match="GROUNDING_ROLE_BASIS_CONFLICT"):
        ExpectedResultV5.model_validate(value)


def test_unknown_is_orthogonal_and_literal_legacy_sentinel_is_not_unknown() -> None:
    value = prediction("UNASSESSED")
    value.update(knowledge_state="UNKNOWN", expected=None)
    assert expected_is_unknown(ExpectedResultV5.model_validate(value))
    value["grounding"]["derivation_role"] = "HYPOTHESIS_ONLY"
    with pytest.raises(ValidationError, match="GROUNDING_UNKNOWN_ROLE_CONFLICT"):
        ExpectedResultV5.model_validate(value)
    value = prediction()
    value["expected"] = "모름"
    assert not expected_is_unknown(ExpectedResultV5.model_validate(value))


@pytest.mark.parametrize("change", [{"expected": "x" * 81}, {"expected": " "}, {"expected": None}])
def test_final_prediction_limits_are_strict(change: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ExpectedResultV5.model_validate({**prediction(), **change})


def test_rationale_and_basis_limits_and_unique_spans() -> None:
    value = prediction("EVIDENCE_INFERENCE")
    value["grounding"]["rationale"] = "x" * 200
    ExpectedResultV5.model_validate(value)
    value["grounding"]["rationale"] += "x"
    with pytest.raises(ValidationError):
        ExpectedResultV5.model_validate(value)
    value = prediction("EVIDENCE_INFERENCE")
    value["basis"] = ["span:0", "span:0"]
    with pytest.raises(ValidationError, match="GROUNDING_DUPLICATE_SPAN"):
        ExpectedResultV5.model_validate(value)
    value["basis"] = [f"span:{i}" for i in range(6)]
    with pytest.raises(ValidationError):
        ExpectedResultV5.model_validate(value)


@pytest.mark.parametrize(
    "extra",
    [
        {"assessment_status": "REVIEWED"},
        {"conclusion_relation": "SUPPORTS"},
        {"schema_version": "2.0.0"},
        {"assessor": "model"},
    ],
)
def test_model_or_reader_cannot_promote_the_assessment(extra: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        RelationAssessmentV1.model_validate(extra)


def test_bindings_must_match_basis_in_order_and_count() -> None:
    value = stored_prediction("EVIDENCE_INFERENCE")
    value["basis"] = ["span:0", "span:1"]
    with pytest.raises(ValidationError, match="GROUNDING_SOURCE_BINDING_MISMATCH"):
        StoredExpectedResultV5.model_validate(value)
    value["source_bindings"].append({**value["source_bindings"][0], "span_id": "span:1"})
    StoredExpectedResultV5.model_validate(value)
    value["source_bindings"].reverse()
    with pytest.raises(ValidationError, match="GROUNDING_SOURCE_BINDING_MISMATCH"):
        StoredExpectedResultV5.model_validate(value)


def test_all_ambiguous_members_are_reported_without_discarding_unrelated_groups() -> None:
    groups = [
        ("t-a", ("h1",)),
        ("t-b", ("h1", "h2", "h1")),
        ("t-a", ("h2",)),
        ("t-c", ("h1", "h2")),
    ]
    issues = prediction_identity_issues(groups)
    assert [(i.test_index, i.row_index, i.reason) for i in issues] == [
        (0, None, "DUPLICATE_TEST_ID"),
        (1, 0, "DUPLICATE_HYPOTHESIS"),
        (1, 2, "DUPLICATE_HYPOTHESIS"),
        (2, None, "DUPLICATE_TEST_ID"),
    ]
    assert groups[3] == ("t-c", ("h1", "h2"))


def test_final_portfolio_rejects_duplicate_tests_and_rows() -> None:
    value = portfolio({"h1": {"h1": "expected"}}).model_dump(mode="json")
    for owner in value["hypotheses"]:
        for test in owner["discriminating_tests"]:
            test["expected_by_hypothesis"] = [prediction()]
    HypothesisPortfolioV5.model_validate(value)
    owner = value["hypotheses"][0]
    owner["discriminating_tests"].append(deepcopy(owner["discriminating_tests"][0]))
    with pytest.raises(ValidationError, match="DUPLICATE_TEST_ID"):
        HypothesisPortfolioV5.model_validate(value)
    owner["discriminating_tests"].pop()
    owner["discriminating_tests"][0]["expected_by_hypothesis"].append(prediction())
    with pytest.raises(ValidationError, match="DUPLICATE_HYPOTHESIS"):
        HypothesisPortfolioV5.model_validate(value)
