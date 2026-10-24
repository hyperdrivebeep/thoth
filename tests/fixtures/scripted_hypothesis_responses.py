"""Schema-aware synthetic model replies, using only the request's supplied context."""

from __future__ import annotations

import re

from pydantic import BaseModel

from thoth.domain.hypothesis import HypothesisPortfolio, HypothesisPortfolioV3
from thoth.domain.hypothesis_v4 import HypothesisPortfolioV4, HypothesisTableFillV4
from thoth.domain.hypothesis_v5_wire import (
    ReceivedHypothesisPortfolioV5,
    ReceivedHypothesisTableFillV5,
)
from thoth.domain.model import ModelRequest


class _FillTask(BaseModel):
    designed_for: str
    test_id: str
    expected_if_true: str
    expected_if_alternative: str
    open_hypothesis_ids: tuple[str, ...]


class _FillContext(BaseModel):
    tests: tuple[_FillTask, ...]


def _korean(request: ModelRequest[BaseModel]) -> bool:
    language = request.response_language or request.context_pack.research_context.get(
        "response_language"
    )
    return language == "ko" or (
        language is None and re.search(r"[가-힣]", request.context_pack.problem) is not None
    )


def _label(korean: bool, own: bool, original: str) -> str:
    if korean:
        return "예상 차이가 나타남" if own else "예상 차이가 나타나지 않음"
    return original


def hypothesis_output[T: BaseModel](
    request: ModelRequest[T], payload: dict[str, object], *, complete: bool = False
) -> T:
    """Keep v2 exact; v3/v4 emit own cells and optionally leave alternatives for the filler."""
    if not issubclass(request.output_model, HypothesisPortfolioV3):
        return request.output_model.model_validate(payload)
    base = HypothesisPortfolio.model_validate(payload)
    korean = _korean(request)
    typed_states = issubclass(request.output_model, HypothesisPortfolioV4)
    hypotheses: list[dict[str, object]] = []
    for owner in base.hypotheses:
        tests: list[dict[str, object]] = []
        for test in owner.discriminating_tests:
            rows: list[dict[str, object]] = []
            for candidate in base.hypotheses:
                own = candidate.hypothesis_id == owner.hypothesis_id
                if own or complete:
                    row: dict[str, object] = {
                        "hypothesis_id": candidate.hypothesis_id,
                        "expected": _label(
                            korean,
                            own,
                            test.expected_if_true if own else test.expected_if_alternative,
                        ),
                        "basis": [],  # A scripted prediction is not an observed result.
                    }
                    if typed_states:
                        row["knowledge_state"] = "PREDICTED"
                    if issubclass(request.output_model, ReceivedHypothesisPortfolioV5):
                        row["grounding"] = {
                            "schema_version": "1.0.0",
                            "derivation_role": "HYPOTHESIS_ONLY",
                            "rationale": "",
                        }
                    rows.append(row)
            tests.append({**test.model_dump(), "expected_by_hypothesis": rows})
        hypotheses.append(
            {
                **owner.model_dump(),
                "discriminating_tests": tests,
                "refutation_conditions": [
                    "독립 비교에서 대안 결과가 확인되면 이 후보를 재검토한다."
                    if korean
                    else (
                        "Reconsider this candidate if an independent check matches the alternative."
                    )
                ]
                if tests
                else [],
            }
        )
    return request.output_model.model_validate({**payload, "hypotheses": hypotheses})


def table_fill_output[T: BaseModel](request: ModelRequest[T]) -> T:
    """Reply only to requested open cells, retaining IDs and the requested v3/v4 schema."""
    context = _FillContext.model_validate(request.context_pack.research_context["table_filler"])
    korean = _korean(request)
    typed_states = issubclass(
        request.output_model, (HypothesisTableFillV4, ReceivedHypothesisTableFillV5)
    )
    tests: list[dict[str, object]] = []
    for test in context.tests:
        rows: list[dict[str, object]] = []
        for hypothesis_id in test.open_hypothesis_ids:
            own = hypothesis_id == test.designed_for
            row: dict[str, object] = {
                "hypothesis_id": hypothesis_id,
                "expected": _label(
                    korean, own, test.expected_if_true if own else test.expected_if_alternative
                ),
                "basis": [],
            }
            if typed_states:
                row["knowledge_state"] = "PREDICTED"
            if issubclass(request.output_model, ReceivedHypothesisTableFillV5):
                row["grounding"] = {
                    "schema_version": "1.0.0",
                    "derivation_role": "HYPOTHESIS_ONLY",
                    "rationale": "",
                }
            rows.append(row)
        tests.append({"designed_for": test.designed_for, "test_id": test.test_id, "rows": rows})
    return request.output_model.model_validate({"tests": tests})
