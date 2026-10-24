"""v5 replies through the established controlled normal-entry model, with no real model calls."""

from dataclasses import replace
from typing import Any

from tests.integration.test_hypothesis_v4_run import LanguageGenerator

from thoth.domain.hypothesis_v4 import HypothesisPortfolioV4, HypothesisTableFillV4
from thoth.domain.hypothesis_v5_wire import (
    ReceivedHypothesisPortfolioV5,
    ReceivedHypothesisTableFillV5,
)


class GroundingGenerator(LanguageGenerator):
    def __init__(self, *, wrong_contract: bool = False) -> None:
        super().__init__()
        self.wrong_contract = wrong_contract

    async def structured(self, request: Any) -> Any:
        if request.output_model is ReceivedHypothesisPortfolioV5:
            result = await super().structured(replace(request, output_model=HypothesisPortfolioV4))
            self.calls[-1] = request
            value = result.output.model_dump(mode="json")
            for owner in value["hypotheses"]:
                for test in owner["discriminating_tests"]:
                    for row in test["expected_by_hypothesis"]:
                        unknown = row["knowledge_state"] == "UNKNOWN"
                        role = (
                            "UNASSESSED"
                            if unknown
                            else "EVIDENCE_INFERENCE"
                            if row["basis"]
                            else "HYPOTHESIS_ONLY"
                        )
                        if unknown:
                            row["basis"] = []
                        row["grounding"] = {
                            "schema_version": "1.0.0",
                            "derivation_role": role,
                            "rationale": (
                                "제공된 관측을 전제로 추론합니다."
                                if request.response_language == "ko"
                                else "Uses the supplied observation as a premise."
                            )
                            if role == "EVIDENCE_INFERENCE"
                            else "",
                        }
            return replace(
                result,
                output=ReceivedHypothesisPortfolioV5.model_validate(value),
                prompt_version="wrong-contract" if self.wrong_contract else request.prompt_version,
            )
        if request.output_model is ReceivedHypothesisTableFillV5:
            result = await super().structured(replace(request, output_model=HypothesisTableFillV4))
            self.calls[-1] = request
            value = result.output.model_dump(mode="json")
            for test in value["tests"]:
                for row in test["rows"]:
                    row["grounding"] = {
                        "schema_version": "1.0.0",
                        "derivation_role": "HYPOTHESIS_ONLY",
                        "rationale": "",
                    }
            return replace(result, output=ReceivedHypothesisTableFillV5.model_validate(value))
        return await super().structured(request)
