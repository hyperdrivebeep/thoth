"""The v3 contract text reaches the model only for the v3 prompt version.

No model is called. The Codex, Claude and xAI adapters build their prompt with `prompt_envelope`
and the OpenAI-compatible adapter calls `role_contract` with the same version, so one check
covers them.
"""

from __future__ import annotations

from datetime import UTC, datetime

from thoth.adapters.models import strict_output_schema
from thoth.adapters.models.codex_oauth import prompt_envelope, role_contract
from thoth.adapters.models.hypothesis_contract_text import hypothesis_contract_addendum
from thoth.application.reducers import SufficiencySignals, assess_information_sufficiency
from thoth.domain.enums import ModelRole
from thoth.domain.hypothesis import HypothesisPortfolio, HypothesisPortfolioV3
from thoth.domain.model import ContextPack, ModelRequest

SHA = "b" * 64


def _request(
    version: str, output_model: type[HypothesisPortfolio]
) -> ModelRequest[HypothesisPortfolio]:
    cutoff = datetime(2026, 10, 6, tzinfo=UTC)
    sufficiency = assess_information_sufficiency(
        assessment_id="assessment:v3",
        assessment_revision_id="revision:v3",
        project_id="project:v3",
        target_object_id="object:v3",
        cutoff_at=cutoff,
        decision_question="What explains the miss?",
        criteria=(),
        evidence=(),
        signals=SufficiencySignals(),
        policy_version="policy:1",
        input_head_set_digest=SHA,
    )
    return ModelRequest(
        role=ModelRole.HYPOTHESIS_GENERATOR,
        project_id="project:v3",
        cutoff_at=cutoff,
        context_pack=ContextPack(
            case_id="case:v3",
            project_id="project:v3",
            object_id="object:v3",
            problem="What explains the miss?",
            evidence=(),
            criteria=(),
            sufficiency=sufficiency,
            input_head_set_digest=SHA,
        ),
        output_model=output_model,
        prompt_version=version,
        model_policy_ref="model-policy:v3",
        max_output_tokens=500,
    )


def test_the_v3_text_is_added_only_for_the_v3_prompt_version() -> None:
    v3 = prompt_envelope(_request("hypothesis_portfolio.v3", HypothesisPortfolioV3))
    v2 = prompt_envelope(_request("hypothesis_portfolio.v2", HypothesisPortfolio))
    assert "CONTRACT_V3" in v3 and "expected_by_hypothesis" in v3 and "refutation_conditions" in v3
    assert "CONTRACT_V3" not in v2 and "expected_by_hypothesis" not in v2
    assert hypothesis_contract_addendum("hypothesis_portfolio.v2") == ""
    assert hypothesis_contract_addendum("") == ""


def test_the_contract_without_a_version_is_the_text_v2_always_had() -> None:
    plain = role_contract("HYPOTHESIS_GENERATOR")
    assert role_contract("HYPOTHESIS_GENERATOR", "hypothesis_portfolio.v2") == plain
    assert "CONTRACT_V3" not in plain
    added = hypothesis_contract_addendum("hypothesis_portfolio.v3")
    assert added and added in role_contract("HYPOTHESIS_GENERATOR", "hypothesis_portfolio.v3")


def test_the_strict_schema_of_the_v3_form_asks_for_the_new_fields() -> None:
    schema = str(strict_output_schema(HypothesisPortfolioV3))
    assert "expected_by_hypothesis" in schema and "refutation_conditions" in schema
    assert "expected_by_hypothesis" not in str(strict_output_schema(HypothesisPortfolio))
