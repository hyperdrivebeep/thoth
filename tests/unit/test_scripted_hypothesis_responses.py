"""Shared QA models honor requested contracts, including the real open-cell filler gate."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal

import pytest
from pydantic import BaseModel
from tests.fixtures.scripted_hypothesis_responses import hypothesis_output
from tests.integration.test_a02_autonomous_acquisition import DynamicA02Model
from tests.integration.test_four_projectpack_portability import (
    GenericProjectPackModel,
    RecordingGenericProjectPackModel,
)
from tests.integration.test_research_request_v2 import ControlledResearchModel

from thoth.application.services.hypothesis_table_filler import fill_tables
from thoth.domain.enums import ModelRole
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.hypothesis import HypothesisPortfolio, HypothesisPortfolioV3, HypothesisTableFill
from thoth.domain.hypothesis_v4 import HypothesisPortfolioV4, HypothesisTableFillV4
from thoth.domain.hypothesis_v5_wire import (
    ReceivedHypothesisPortfolioV5,
    ReceivedHypothesisTableFillV5,
)
from thoth.domain.model import ContextPack, ModelRequest

CUTOFF = datetime(2026, 10, 23, tzinfo=UTC)


def model_for(name: str):
    if name == "generic":
        return GenericProjectPackModel()
    if name == "dynamic":
        return DynamicA02Model()
    return ControlledResearchModel(one=True, tested=True)


def request_for[T: BaseModel](
    output: type[T], role: ModelRole, language: Literal["ko", "en"]
) -> ModelRequest[T]:
    span = EvidenceSpan.model_validate(
        {
            "span_id": "span:scripted",
            "project_id": "project:scripted",
            "artifact_id": "artifact:scripted",
            "source_version_id": "source:scripted",
            "locator": {"line": 1},
            "exact_text": "synthetic",
            "text_sha256": "a" * 64,
            "extraction_method": "synthetic",
            "support_state": "EXTRACTED",
            "authority_state": "UNCLASSIFIED",
            "verification_state": "NOT_CHECKED",
            "cutoff_state": "ELIGIBLE",
        }
    )
    context = ContextPack(
        case_id="case:scripted",
        project_id="project:scripted",
        object_id="object:scripted",
        problem="원인을 비교해 주세요",
        evidence=(span,),
        criteria=(),
        sufficiency=None,
        input_head_set_digest="b" * 64,
    )
    return ModelRequest(
        role=role,
        project_id=context.project_id,
        cutoff_at=CUTOFF,
        context_pack=context,
        output_model=output,
        prompt_version="controlled-contract",
        model_policy_ref="model-policy:scripted",
        max_output_tokens=1000,
        response_language=language,
    )


@pytest.mark.parametrize("name", ["generic", "dynamic", "controlled"])
@pytest.mark.parametrize(
    "output",
    [
        HypothesisPortfolio,
        HypothesisPortfolioV3,
        HypothesisPortfolioV4,
        ReceivedHypothesisPortfolioV5,
    ],
)
@pytest.mark.parametrize("language", ["ko", "en"])
async def test_models_generate_the_requested_version_with_stable_ids(
    name: str, output: type[HypothesisPortfolio], language: Literal["ko", "en"]
) -> None:
    model = model_for(name)
    request = request_for(output, ModelRole.HYPOTHESIS_GENERATOR, language)
    result = await model.structured(request)
    assert type(result.output) is output
    assert len(result.output.hypotheses) == (1 if name == "controlled" else 3)
    for owner in result.output.hypotheses:
        if isinstance(result.output, HypothesisPortfolioV3):
            row = owner.model_dump(mode="json")["discriminating_tests"][0][
                "expected_by_hypothesis"
            ][0]
            assert row["hypothesis_id"] == owner.hypothesis_id and row["basis"] == []
            assert ("knowledge_state" in row) is issubclass(output, HypothesisPortfolioV4)
            if output is ReceivedHypothesisPortfolioV5:
                assert row["grounding"] == {
                    "schema_version": "1.0.0",
                    "derivation_role": "HYPOTHESIS_ONLY",
                    "rationale": "",
                }
            assert ("차이" in row["expected"]) is (language == "ko")
        else:
            assert "refutation_conditions" not in owner.model_dump()


@pytest.mark.parametrize("name", ["generic", "dynamic", "controlled"])
@pytest.mark.parametrize(
    "output", [HypothesisTableFill, HypothesisTableFillV4, ReceivedHypothesisTableFillV5]
)
async def test_models_answer_only_the_requested_open_cells(
    name: str, output: type[HypothesisTableFill] | type[ReceivedHypothesisTableFillV5]
) -> None:
    request = request_for(output, ModelRole.HYPOTHESIS_TABLE_FILLER, "en")
    context = request.context_pack.model_copy(
        update={
            "evidence": (),
            "research_context": {
                "table_filler": {
                    "tests": [
                        {
                            "designed_for": "h1",
                            "test_id": "t1",
                            "expected_if_true": "different",
                            "expected_if_alternative": "same",
                            "open_hypothesis_ids": ["h2", "h3"],
                        }
                    ]
                }
            },
        }
    )
    result = await model_for(name).structured(replace(request, context_pack=context))
    assert type(result.output) is output
    (test,) = result.output.tests
    assert test.designed_for == "h1" and test.test_id == "t1"
    assert [(row.hypothesis_id, row.expected, row.basis) for row in test.rows] == [
        ("h2", "same", ()),
        ("h3", "same", ()),
    ]


@dataclass(frozen=True)
class FillCommand:
    cutoff_at: datetime
    model_policy_ref: str
    evidence: tuple[EvidenceSpan, ...]


@pytest.mark.parametrize("complete", [False, True])
async def test_product_calls_filler_only_for_open_tables(complete: bool) -> None:
    model = RecordingGenericProjectPackModel(complete_tables=complete)
    request = request_for(HypothesisPortfolioV3, ModelRole.HYPOTHESIS_GENERATOR, "en")
    generated = await model.structured(request)
    context = request.context_pack.model_copy(
        update={"research_context": {"response_language": "en"}}
    )
    filled = await fill_tables(
        model,
        generated.output,
        context,
        FillCommand(CUTOFF, request.model_policy_ref, context.evidence),
    )
    assert isinstance(filled, HypothesisPortfolioV3) and filled.table_fill is not None
    assert filled.table_fill.state == ("SKIPPED_FULL" if complete else "CALLED")
    assert sum(call.role == ModelRole.HYPOTHESIS_TABLE_FILLER for call in model.calls) == (
        0 if complete else 1
    )
    assert (
        sum(
            len(test.expected_by_hypothesis)
            for h in filled.hypotheses
            for test in h.discriminating_tests
        )
        == 9
    )


async def test_v2_payload_is_not_enriched() -> None:
    request = request_for(HypothesisPortfolio, ModelRole.HYPOTHESIS_GENERATOR, "en")
    original = (await GenericProjectPackModel().structured(request)).output
    assert hypothesis_output(request, original.model_dump()).model_dump() == original.model_dump()
