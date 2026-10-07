"""Contract v3: the generator may also say what each hypothesis expects from a test.

The new fields are optional and live only on v3 subclasses, so the v2 output form the model sees is
exactly what it was. A v2 record still reads as v3, a v3 record dumps its extra fields wherever it
is dumped as a portfolio, and what the model gets wrong about ids is dropped item by item.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from thoth.application.services.hypothesis_contract_v3 import (
    CONTRACT_V2,
    CONTRACT_V3,
    DroppedExpectation,
    generator_contract,
    settle_contract,
)
from thoth.domain.enums import (
    CausalDepth,
    HypothesisStatus,
    PortfolioStatus,
    Reversibility,
    RiskTier,
)
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.hypothesis import (
    DiscriminatingTest,
    ExpectedResult,
    Hypothesis,
    HypothesisPortfolio,
    HypothesisPortfolioV3,
    is_v3,
    portfolio_from_json,
)

HEAD = "a" * 64


def a_test_dict(test_id: str = "t1", **extra: Any) -> dict[str, Any]:
    return {
        "test_id": test_id,
        "procedure_candidate": "compare",
        "expected_if_true": "worse",
        "expected_if_alternative": "same",
        "risk_tier": RiskTier.R0,
        "reversibility": Reversibility.FULL,
        **extra,
    }


def hypothesis_dict(hypothesis_id: str, **extra: Any) -> dict[str, Any]:
    return {
        "hypothesis_id": hypothesis_id,
        "object_id": "object:o",
        "statement": "a cause",
        "observed_problem": "a problem",
        "primary_locus": None,
        "causal_depth": CausalDepth.UNDETERMINED,
        "scope_conditions": {"condition": "alpha"},
        "support_evidence_refs": ("span:a",),
        "counterevidence_refs": (),
        "counterevidence_queries": ("look for a counterexample",),
        "assumptions": (),
        "uncertainty": "unvalidated",
        "predicted_observations": (),
        "discriminating_tests": (a_test_dict(),),
        "status": HypothesisStatus.DRAFT,
        **extra,
    }


def portfolio_dict(*hypotheses: dict[str, Any]) -> dict[str, Any]:
    return {
        "portfolio_id": "portfolio:o",
        "object_id": "object:o",
        "hypotheses": hypotheses,
        "status": PortfolioStatus.DRAFT,
        "generated_from_head_set": HEAD,
    }


def spans(*ids: str) -> tuple[EvidenceSpan, ...]:
    class Span:  # only the id is read
        def __init__(self, span_id: str) -> None:
            self.span_id = span_id

    return tuple(Span(item) for item in ids)  # type: ignore[misc]


def test_a_v2_portfolio_reads_as_v3_with_nothing_filled_in() -> None:
    plain = portfolio_dict(hypothesis_dict("h1"), hypothesis_dict("h2"))
    v3 = HypothesisPortfolioV3.model_validate(plain)
    assert all(h.refutation_conditions == () for h in v3.hypotheses)
    assert all(
        t.expected_by_hypothesis == () for h in v3.hypotheses for t in h.discriminating_tests
    )


def test_the_v2_form_does_not_accept_or_show_the_new_fields() -> None:
    extra = portfolio_dict(
        hypothesis_dict("h1", refutation_conditions=("x",)), hypothesis_dict("h2")
    )
    with pytest.raises(ValidationError):
        HypothesisPortfolio.model_validate(extra)
    # the form the model is shown for v2 is exactly what it was: no new name appears in it
    schema = json.dumps(HypothesisPortfolio.model_json_schema())
    assert "expected_by_hypothesis" not in schema and "refutation_conditions" not in schema
    assert "expected_by_hypothesis" in json.dumps(HypothesisPortfolioV3.model_json_schema())


def test_a_v3_portfolio_keeps_its_extra_fields_when_dumped_as_a_portfolio() -> None:
    filled = a_test_dict(
        expected_by_hypothesis=({"hypothesis_id": "h2", "expected": "same", "basis": ("span:a",)},)
    )
    v3 = HypothesisPortfolioV3.model_validate(
        portfolio_dict(
            hypothesis_dict(
                "h1", discriminating_tests=(filled,), refutation_conditions=("if both runs match",)
            ),
            hypothesis_dict("h2"),
        )
    )
    # a v2-typed holder still dumps what the object has
    holder = HypothesisPortfolio.model_construct(
        **{k: getattr(v3, k) for k in HypothesisPortfolio.model_fields}
    )
    dumped = holder.model_dump(mode="json")
    first = dumped["hypotheses"][0]
    assert first["refutation_conditions"] == ["if both runs match"]
    assert first["discriminating_tests"][0]["expected_by_hypothesis"][0]["expected"] == "same"
    assert portfolio_from_json(dumped, v3=True).hypotheses[0].refutation_conditions == (
        "if both runs match",
    )
    assert portfolio_from_json(
        json.loads(
            HypothesisPortfolio.model_validate(
                portfolio_dict(hypothesis_dict("h1"), hypothesis_dict("h2"))
            ).model_dump_json()
        ),
        v3=False,
    )


def test_the_form_is_chosen_by_the_version_never_by_words_in_the_text() -> None:
    # a v2 portfolio whose sentences happen to contain the names of the v3 fields stays v2
    text = "the expected_by_hypothesis and refutation_conditions fields are discussed here"
    plain = portfolio_dict(hypothesis_dict("h1", statement=text), hypothesis_dict("h2"))
    read = portfolio_from_json(plain, v3=False)
    assert type(read) is HypothesisPortfolio
    assert read.hypotheses[0].statement == text
    # a v3 portfolio reads as v3 even when nothing was filled in
    assert type(portfolio_from_json(plain, v3=True)) is HypothesisPortfolioV3
    # and the version follows the portfolio it replaces
    assert is_v3(HypothesisPortfolioV3.model_validate(plain)) is True
    assert is_v3(HypothesisPortfolio.model_validate(plain)) is False


def test_too_many_conditions_or_a_probability_like_field_are_not_accepted() -> None:
    with pytest.raises(ValidationError):
        HypothesisPortfolioV3.model_validate(
            portfolio_dict(
                hypothesis_dict("h1", refutation_conditions=tuple(f"c{i}" for i in range(6))),
                hypothesis_dict("h2"),
            )
        )
    with pytest.raises(ValidationError):
        ExpectedResult(hypothesis_id="h1", expected="x", basis=(), probability=0.8)  # type: ignore[call-arg]


def test_the_contract_is_v2_unless_the_work_asks_for_v3() -> None:
    off = generator_contract(False)
    on = generator_contract(True)
    assert (off["prompt_version"], off["output_model"]) == (CONTRACT_V2, HypothesisPortfolio)
    assert (on["prompt_version"], on["output_model"]) == (CONTRACT_V3, HypothesisPortfolioV3)
    assert CONTRACT_V2 == "hypothesis_portfolio.v2" and CONTRACT_V3 == "hypothesis_portfolio.v3"


def _filled(*rows: dict[str, Any]) -> dict[str, Any]:
    return a_test_dict(expected_by_hypothesis=rows)


def test_an_item_that_names_an_unknown_hypothesis_or_span_is_dropped_alone_and_recorded() -> None:
    good = {"hypothesis_id": "h2", "expected": "same", "basis": ("span:a",)}
    unknown_hypothesis = {"hypothesis_id": "h9", "expected": "same", "basis": ()}
    unknown_span = {"hypothesis_id": "h3", "expected": "worse", "basis": ("span:made-up",)}
    twice = {"hypothesis_id": "h2", "expected": "other", "basis": ()}
    own = {"hypothesis_id": "h1", "expected": "worse", "basis": ()}
    v3 = HypothesisPortfolioV3.model_validate(
        portfolio_dict(
            hypothesis_dict(
                "h1",
                discriminating_tests=(_filled(own, good, unknown_hypothesis, unknown_span, twice),),
            ),
            hypothesis_dict("h2"),
            hypothesis_dict("h3"),
        )
    )
    settled = settle_contract(v3, spans("span:a"))
    assert isinstance(settled, HypothesisPortfolioV3)
    rows = settled.hypotheses[0].discriminating_tests[0].expected_by_hypothesis
    assert [(r.hypothesis_id, r.expected) for r in rows] == [("h1", "worse"), ("h2", "same")]
    dropped = settled.hypotheses[0].dropped
    assert {(d.hypothesis_id, d.reason) for d in dropped} == {
        ("h9", "UNKNOWN_HYPOTHESIS"),
        ("h3", "UNKNOWN_SPAN"),
        ("h2", "DUPLICATE_HYPOTHESIS"),
    }
    assert all(isinstance(d, DroppedExpectation) and d.test_id == "t1" for d in dropped)
    assert settled.hypotheses[1].discriminating_tests[0].expected_by_hypothesis == ()


def test_a_v2_output_passes_through_untouched() -> None:
    plain = HypothesisPortfolio.model_validate(
        portfolio_dict(hypothesis_dict("h1"), hypothesis_dict("h2"))
    )
    assert settle_contract(plain, spans("span:a")) is plain
    assert DiscriminatingTest.model_validate(a_test_dict()).test_id == "t1"
    assert Hypothesis.model_validate(hypothesis_dict("h1")).hypothesis_id == "h1"
