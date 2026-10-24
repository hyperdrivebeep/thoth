"""Synthetic model inputs and a recording, network-free model port for v5 tests."""

from datetime import UTC, datetime
from typing import Any, cast

from pydantic import BaseModel
from tests.fixtures.hypothesis_grounding_v5 import stage_input
from tests.unit.application.test_hypothesis_table_filler import portfolio
from tests.unit.test_research_short_text_selection import source

from thoth.application.services.hypothesis_grounding_v5 import grounding_generator_contract
from thoth.domain.enums import ModelRole
from thoth.domain.hypothesis_v5_wire import ReceivedHypothesisPortfolioV5
from thoth.domain.model import ContextPack, ModelRequest, ModelResult
from thoth.domain.model_dispatch import CONTROLLED_MODEL_CONTROL
from thoth.domain.research_execution import ResearchBoundary, ResearchWork
from thoth.domain.research_reference import RevisionRef
from thoth.domain.response_language import ResponseLanguage


def raw_portfolio(tables: dict[str, dict[str, str]]) -> ReceivedHypothesisPortfolioV5:
    value = portfolio(tables).model_dump(mode="json")
    for owner in value["hypotheses"]:
        for test in owner["discriminating_tests"]:
            for row in test["expected_by_hypothesis"]:
                unknown = row["expected"] == "모름"
                row.update(
                    knowledge_state="UNKNOWN" if unknown else "PREDICTED",
                    expected=None if unknown else row["expected"],
                    grounding={
                        "derivation_role": "UNASSESSED" if unknown else "HYPOTHESIS_ONLY",
                        "rationale": "",
                    },
                )
    return ReceivedHypothesisPortfolioV5.model_validate(value)


def wire_context() -> ContextPack:
    return ContextPack(
        case_id="case:wire",
        project_id="p",
        object_id="object:o",
        problem="Explain the problem",
        evidence=source(8),
        criteria=(),
        sufficiency=None,
        input_head_set_digest="a" * 64,
    )


def fill_row(target: str, expected: str = "same", **changes: Any) -> dict[str, Any]:
    return {
        "hypothesis_id": target,
        "knowledge_state": "PREDICTED",
        "expected": expected,
        "basis": [],
        "grounding": {"derivation_role": "HYPOTHESIS_ONLY", "rationale": ""},
        **changes,
    }


def filler_payload(*rows: dict[str, Any]) -> dict[str, Any]:
    return {"tests": [{"designed_for": "h1", "test_id": "t-h1", "rows": list(rows)}]}


class Boundary:
    def check(self) -> None:
        pass

    def call_timeout(self) -> float:
        return 5

    def new_model_call(self) -> str:
        return "dispatch:fake"

    def usage_observation(self) -> dict[str, object]:
        return {"input_tokens": 11}


def research_fixture(language: ResponseLanguage | None = None) -> ResearchWork:
    return ResearchWork(
        request_ref=RevisionRef.model_validate(stage_input()["generating_request_ref"]),
        effective_question="Explain",
        boundary=cast(ResearchBoundary, Boundary()),
        response_language=language,
    )


def generator_request() -> ModelRequest[ReceivedHypothesisPortfolioV5]:
    return ModelRequest(
        role=ModelRole.HYPOTHESIS_GENERATOR,
        project_id="p",
        cutoff_at=datetime(2026, 10, 24, tzinfo=UTC),
        context_pack=wire_context(),
        output_model=ReceivedHypothesisPortfolioV5,
        prompt_version="hypothesis_portfolio.v5",
        model_policy_ref="policy:fixture",
        max_output_tokens=grounding_generator_contract()["max_output_tokens"],
    )


class RecordingGroundingModel:
    control_capability = CONTROLLED_MODEL_CONTROL

    def __init__(self, reply: dict[str, Any] | None = None, error: BaseException | None = None):
        self.reply = reply or filler_payload(fill_row("h2"), fill_row("h3"))
        self.error = error
        self.calls: list[ModelRequest[Any]] = []

    async def structured[T: BaseModel](self, request: ModelRequest[T]) -> ModelResult[T]:
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        value = (
            raw_portfolio({"h1": {"h1": "worse"}}).model_dump(mode="json")
            if request.role == ModelRole.HYPOTHESIS_GENERATOR
            else self.reply
        )
        return ModelResult(
            output=request.output_model.model_validate(value),
            model_id="fake",
            prompt_version=request.prompt_version,
            scripted=True,
            input_digest="c" * 64,
            output_digest="d" * 64,
        )
