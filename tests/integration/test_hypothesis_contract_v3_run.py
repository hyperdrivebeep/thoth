"""Hypothesis generation contract v3: off by default, the same calls when on, and a settled record.

A project turns it on in its model-call settings. When on, the one generator call is shown the v3
form and contract text, what it says about expected results and refutation conditions is stored
beside the hypothesis (items that name an unknown hypothesis or span are dropped alone and
recorded), and the number of model calls is the same. A controlled model plays the generator; no
real model is called.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from tests.integration.test_hypothesis_link import investigate
from tests.integration.test_research_request_v2 import ControlledResearchModel
from tests.integration.test_trace_origin import RAIN, Rpc, opened

from thoth.application.services.hypothesis_projection import hypothesis_view
from thoth.domain.enums import ModelRole
from thoth.domain.hypothesis import HypothesisPortfolioV3, HypothesisV3
from thoth.domain.hypothesis_full import HypothesisRecord

CONTRACT_V2 = "hypothesis_portfolio.v2"
CONTRACT_V3 = "hypothesis_portfolio.v3"
SAID = "if both runs match, drop it"


class GeneratorModel(ControlledResearchModel):
    """The controlled model; when it is shown the v3 form it fills it in, with some bad ids."""

    def __init__(self) -> None:
        super().__init__(one=True, tested=True)

    async def structured(self, request: Any) -> Any:
        result = await super().structured(request)
        if request.role != ModelRole.HYPOTHESIS_GENERATOR:
            return result
        if request.output_model is not HypothesisPortfolioV3:
            return result
        first = result.output.hypotheses[0]
        second_id = first.hypothesis_id + ":other"
        span = tuple(item.span_id for item in request.context_pack.evidence)[:1]

        def rows(own: str, *items: tuple[str, str, tuple[str, ...]]) -> list[dict[str, Any]]:
            return [{"hypothesis_id": h, "expected": e, "basis": list(b)} for h, e, b in items]

        def v3(hypothesis_id: str, tests: list[list[dict[str, Any]]], conditions: tuple[str, ...]):
            shaped = [
                {**test.model_dump(), "expected_by_hypothesis": rows_for}
                for test, rows_for in zip(first.discriminating_tests, tests, strict=True)
            ]
            return HypothesisV3.model_validate(
                {
                    **first.model_dump(),
                    "hypothesis_id": hypothesis_id,
                    "discriminating_tests": shaped,
                    "refutation_conditions": conditions,
                }
            )

        one = [
            rows("a", (first.hypothesis_id, "worse", span), (second_id, "same", ()))
            + rows(
                "a", ("hypothesis:nobody", "same", ()), (second_id, "again", ())
            )  # two bad items
            + rows("a", (second_id + "2", "worse", ("span:made-up",))),
            rows("a", (first.hypothesis_id, "worse", ()), (second_id, "모름", ())),
        ]
        portfolio = result.output.model_copy(
            update={
                "hypotheses": (
                    v3(first.hypothesis_id, one, (SAID,)),
                    v3(second_id, [[], []], ()),
                )
            }
        )
        return replace(result, output=portfolio)


async def settings(rpc: Rpc) -> dict[str, Any]:
    return await rpc("model/callSettings/read", project_id="p")


async def turn_on(rpc: Rpc) -> None:
    current = await settings(rpc)
    await rpc(
        "model/callSettings/update",
        project_id="p",
        hypothesis_contract_v3=True,
        expected_digest=current["settings_digest"],
    )


def generator_calls(model: ControlledResearchModel) -> list[Any]:
    return [call for call in model.calls if call.role == ModelRole.HYPOTHESIS_GENERATOR]


async def run(tmp_path: Path, *, on: bool) -> tuple[Any, Any, Rpc, str, GeneratorModel]:
    model = GeneratorModel()
    runtime, rpc = await opened(tmp_path, model)
    if on:
        await turn_on(rpc)
    thread_id = await investigate(runtime, rpc, RAIN)
    return runtime, model, rpc, thread_id, model


async def portfolio_of(rpc: Rpc, thread_id: str) -> dict[str, Any]:
    view = await rpc("thread/read", project_id="p", thread_id=thread_id, view="FULL")
    return view["current_result"]["result"]["portfolio"]


@pytest.mark.asyncio
async def test_the_switch_is_off_by_default_and_each_switch_keeps_the_other(tmp_path: Path) -> None:
    runtime, rpc = await opened(tmp_path, GeneratorModel())
    try:
        first = await settings(rpc)
        assert (first["hypothesis_contract_v3"], first["auto_retry_interrupted_model_call"]) == (
            False,
            False,
        )
        await turn_on(rpc)
        second = await settings(rpc)
        assert second["hypothesis_contract_v3"] is True
        assert second["auto_retry_interrupted_model_call"] is False  # left out: kept
        await rpc(
            "model/callSettings/update",
            project_id="p",
            auto_retry_interrupted_model_call=True,
            expected_digest=second["settings_digest"],
        )
        third = await settings(rpc)
        assert (third["hypothesis_contract_v3"], third["auto_retry_interrupted_model_call"]) == (
            True,
            True,
        )
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_off_the_generator_sees_v2_and_nothing_of_v3_is_stored_or_shown(
    tmp_path: Path,
) -> None:
    runtime, model, rpc, thread_id, _ = await run(tmp_path, on=False)
    try:
        (call,) = generator_calls(model)
        assert (
            call.prompt_version == CONTRACT_V2
            and call.output_model.__name__ == "HypothesisPortfolio"
        )
        portfolio = await portfolio_of(rpc, thread_id)
        dumped = str(portfolio)
        assert "expected_by_hypothesis" not in dumped and "refutation_conditions" not in dumped
        (item,) = (await rpc("hypothesis/list", project_id="p"))["hypotheses"]
        record = (
            await rpc("hypothesis/read", project_id="p", hypothesis_id=item["hypothesis_id"])
        )["hypothesis"]
        assert record["generation_details"]["contract_version"] is None
        assert record["generation_details"]["expected_results"] == []
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_on_the_generator_sees_v3_and_what_it_says_is_stored_and_shown_with_bad_items_dropped(
    tmp_path: Path,
) -> None:
    runtime, model, rpc, thread_id, _ = await run(tmp_path, on=True)
    try:
        (call,) = generator_calls(model)
        assert call.prompt_version == CONTRACT_V3 and call.output_model is HypothesisPortfolioV3
        portfolio = await portfolio_of(rpc, thread_id)
        first, second = portfolio["hypotheses"]
        assert first["refutation_conditions"] == [SAID] and second["refutation_conditions"] == []
        tests = {t["test_id"]: t["expected_by_hypothesis"] for t in first["discriminating_tests"]}
        assert [(r["hypothesis_id"], r["expected"]) for r in tests["test-1"]] == [
            (first["hypothesis_id"], "worse"),
            (second["hypothesis_id"], "same"),
        ]  # the two bad items are gone, the rest of the table stands
        assert [r["expected"] for r in tests["test-2"]] == ["worse", "모름"]
        read = (await rpc("hypothesis/read", project_id="p", hypothesis_id=first["hypothesis_id"]))[
            "hypothesis"
        ]
        stored = read["generation_details"]
        assert stored["contract_version"] == CONTRACT_V3
        # read back from the record, the hypothesis shows what the generator said (the projection
        # a later step, such as a counter-search, hands on)
        viewed = hypothesis_view(HypothesisRecord.model_validate(read))
        assert isinstance(viewed, HypothesisV3) and viewed.refutation_conditions == (SAID,)
        assert {t.test_id: len(t.expected_by_hypothesis) for t in viewed.discriminating_tests} == {
            "test-1": 2,
            "test-2": 2,
        }
        assert stored["refutation_conditions"] == [SAID]
        assert {e["test_id"] for e in stored["expected_results"]} == {"test-1", "test-2"}
        assert {(d["test_id"], d["reason"]) for d in stored["dropped_expected_results"]} == {
            ("test-1", "UNKNOWN_HYPOTHESIS"),
            ("test-1", "DUPLICATE_HYPOTHESIS"),
        }
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_v3_makes_the_same_number_of_model_calls_and_the_value_it_ran_with_stays(
    tmp_path: Path,
) -> None:
    off_runtime, off_model, _, _, _ = await run(tmp_path / "off", on=False)
    on_runtime, on_model, on_rpc, thread_id, _ = await run(tmp_path / "on", on=True)
    try:
        assert len(on_model.calls) == len(off_model.calls)
        assert [c.role for c in on_model.calls] == [c.role for c in off_model.calls]
        # the project switches it off afterwards: what was made stays what it was made with
        current = await settings(on_rpc)
        await on_rpc(
            "model/callSettings/update",
            project_id="p",
            hypothesis_contract_v3=False,
            expected_digest=current["settings_digest"],
        )
        first = (await portfolio_of(on_rpc, thread_id))["hypotheses"][0]
        assert first["refutation_conditions"] == [SAID]
        (hypothesis, *_) = (await on_rpc("hypothesis/list", project_id="p"))["hypotheses"]
        stored = (
            await on_rpc(
                "hypothesis/read", project_id="p", hypothesis_id=hypothesis["hypothesis_id"]
            )
        )["hypothesis"]["generation_details"]
        assert stored["contract_version"] == CONTRACT_V3
    finally:
        off_runtime.close()
        on_runtime.close()
