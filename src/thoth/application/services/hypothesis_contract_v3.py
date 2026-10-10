"""Hypothesis generation contract v3 (off unless the project turns it on).

The same single generator call is made either way; only the form it is shown and the contract text
change. In v3 each discriminating test may say what every hypothesis expects from it (a short label,
"모름" when not known) and each hypothesis may say what result would make it give the hypothesis up.
What the model gets wrong about ids is dropped item by item and recorded, never failing the call.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypedDict

from thoth.application.services.text_script import (
    HAN,
    HANGUL,
    KATAKANA_MIDDLE_DOT,
    Quotes,
    korean_middle_dot,
    language_problem,
    visible_problem,
)
from thoth.domain.enums import HypothesisStatus
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.hypothesis import (
    DiscriminatingTest,
    DiscriminatingTestV3,
    ExpectedResult,
    Hypothesis,
    HypothesisPortfolio,
    HypothesisPortfolioV3,
    HypothesisV3,
)
from thoth.domain.research_projection import DroppedExpectation, ExpectedByTest

CONTRACT_V2 = "hypothesis_portfolio.v2"
CONTRACT_V3 = "hypothesis_portfolio.v3.3"
REFUTATION_LANGUAGE_MISMATCH = "REFUTATION_LANGUAGE_MISMATCH"
REFUTATION_SCRIPT_MISMATCH = "REFUTATION_SCRIPT_MISMATCH"
# The readable lists of a hypothesis, and what is made of one that is in the wrong script.
LIST_TEXT_FIELDS = (
    "missing_evidence",
    "counterevidence_queries",
    "predicted_observations",
    "assumptions",
)


class V3DetailFields(TypedDict, total=False):
    refutation_conditions: tuple[str, ...]
    expected_results: tuple[ExpectedByTest, ...]
    dropped_expected_results: tuple[DroppedExpectation, ...]
    contract_version: str


class GeneratorContract(TypedDict):
    output_model: type[HypothesisPortfolio]
    prompt_version: str


def generator_contract(v3: bool) -> GeneratorContract:
    """The form and contract version for the generator call; v2 exactly as it always was."""
    if v3:
        return {"output_model": HypothesisPortfolioV3, "prompt_version": CONTRACT_V3}
    return {"output_model": HypothesisPortfolio, "prompt_version": CONTRACT_V2}


def base_tests(candidate: Hypothesis) -> tuple[DiscriminatingTest, ...]:
    """The tests as the v2 record keeps them; a v3 test's extra fields are kept apart."""
    if not isinstance(candidate, HypothesisV3):
        return candidate.discriminating_tests
    return tuple(
        DiscriminatingTest.model_validate(item.model_dump(exclude={"expected_by_hypothesis"}))
        for item in candidate.discriminating_tests
    )


def v3_details(candidate: Hypothesis) -> V3DetailFields:
    """The generation-details fields a v3 hypothesis adds; a v2 hypothesis adds none."""
    if not isinstance(candidate, HypothesisV3):
        return {}
    return {
        "refutation_conditions": candidate.refutation_conditions,
        "expected_results": tuple(
            ExpectedByTest(test_id=item.test_id, rows=item.expected_by_hypothesis)
            for item in candidate.discriminating_tests
            if item.expected_by_hypothesis
        ),
        "dropped_expected_results": tuple(candidate.dropped),
        "contract_version": CONTRACT_V3,
    }


@dataclass(frozen=True)
class _Settled:
    hypothesis: HypothesisV3
    dropped: tuple[DroppedExpectation, ...]


def _refutation_language(
    hypothesis: HypothesisV3, problem: str
) -> tuple[tuple[str, ...], list[DroppedExpectation]]:
    """The refutation conditions that stay in the question's language, and what was dropped.

    A question with Hangul gets Hangul sentences: a condition with no Hangul is dropped, and so
    is one with Chinese characters unless the question itself has some. A question without
    Hangul leaves every condition as it was. Each drop keeps the original text.
    """
    conditions = hypothesis.refutation_conditions
    if not HANGUL.search(problem):
        return conditions, []
    dropped: list[DroppedExpectation] = []

    def keep(reason: str, bad: Callable[[str], bool], items: tuple[str, ...]) -> tuple[str, ...]:
        dropped.extend(
            DroppedExpectation(
                test_id="", hypothesis_id=hypothesis.hypothesis_id, reason=reason, text=item
            )
            for item in items
            if bad(item)
        )
        return tuple(item for item in items if not bad(item))

    kept = keep(REFUTATION_LANGUAGE_MISMATCH, lambda item: not HANGUL.search(item), conditions)
    if not HAN.search(problem):
        kept = keep(REFUTATION_SCRIPT_MISMATCH, lambda item: bool(HAN.search(item)), kept)
    return kept, dropped


def _flag(hypothesis_id: str, test_id: str, field: str, text: str, kind: str) -> DroppedExpectation:
    return DroppedExpectation(
        test_id=test_id,
        hypothesis_id=hypothesis_id,
        reason=f"{field.upper()}_{kind}_FLAGGED",
        text=text,
    )


def _visible_text(
    hypothesis: HypothesisV3, problem: str, quotes: Quotes
) -> tuple[dict[str, tuple[str, ...]], list[DroppedExpectation]]:
    """What a Hangul question leaves of the readable text of a hypothesis.

    A list item (missing evidence, counterevidence queries, predicted observations, assumptions) in
    the wrong language or script is dropped and recorded with its text, unless dropping the last one
    would leave a record the model forbids (no support refs and no gap; no counterevidence ref and
    no query; a non-draft hypothesis with no prediction): then it stays and is flagged. A single
    text (statement, uncertainty, a test's procedure and expected texts) is never removed; a
    problem is only recorded as FLAGGED.
    """
    updates: dict[str, tuple[str, ...]] = {}
    records: list[DroppedExpectation] = []
    if not HANGUL.search(problem):
        return updates, records
    hypothesis_id = hypothesis.hypothesis_id
    guarded = {
        "missing_evidence": not hypothesis.support_evidence_refs,
        "counterevidence_queries": not hypothesis.counterevidence_refs,
        "predicted_observations": hypothesis.status != HypothesisStatus.DRAFT,
    }
    for field in LIST_TEXT_FIELDS:
        items: tuple[str, ...] = getattr(hypothesis, field)
        found = [(item, visible_problem(item, problem, quotes)) for item in items]
        bad = [(item, kind) for item, kind in found if kind is not None]
        if not bad:
            continue
        keep = tuple(item for item, kind in found if kind is None)
        if not keep and guarded.get(field, False):
            records.extend(_flag(hypothesis_id, "", field, item, kind) for item, kind in bad)
            continue
        updates[field] = keep
        records.extend(
            DroppedExpectation(
                test_id="",
                hypothesis_id=hypothesis_id,
                reason=f"{field.upper()}_{kind}_MISMATCH",
                text=item,
            )
            for item, kind in bad
        )
    for field in ("statement", "uncertainty"):
        text = getattr(hypothesis, field)
        if (kind := visible_problem(text, problem, quotes)) is not None:
            records.append(_flag(hypothesis_id, "", field, text, kind))
    for test in hypothesis.discriminating_tests:
        for field in ("procedure_candidate", "expected_if_true", "expected_if_alternative"):
            text = getattr(test, field)
            if (kind := visible_problem(text, problem, quotes)) is not None:
                records.append(_flag(hypothesis_id, test.test_id, field, text, kind))
    return updates, records


def _map_texts(hypothesis: HypothesisV3, change: Callable[[str], str]) -> HypothesisV3:
    """The hypothesis with every readable text passed through change (ids and numbers untouched)."""
    tests = tuple(
        test.model_copy(
            update={
                "procedure_candidate": change(test.procedure_candidate),
                "expected_if_true": change(test.expected_if_true),
                "expected_if_alternative": change(test.expected_if_alternative),
                "expected_by_hypothesis": tuple(
                    row.model_copy(update={"expected": change(row.expected)})
                    for row in test.expected_by_hypothesis
                ),
            }
        )
        for test in hypothesis.discriminating_tests
    )
    lists = {
        field: tuple(change(item) for item in getattr(hypothesis, field))
        for field in (*LIST_TEXT_FIELDS, "refutation_conditions")
    }
    return hypothesis.model_copy(
        update={
            "statement": change(hypothesis.statement),
            "uncertainty": change(hypothesis.uncertainty),
            "discriminating_tests": tests,
            **lists,
        }
    )


def _with_korean_dots(hypothesis: HypothesisV3, problem: str, quotes: Quotes) -> HypothesisV3:
    """A Hangul question gets the Korean middle dot where the model wrote the katakana one, in every
    readable text and without a record. A dot the question or the evidence has is a quotation and
    stays."""
    if not HANGUL.search(problem):
        return hypothesis
    seen: list[None] = []

    def change(text: str) -> str:
        if KATAKANA_MIDDLE_DOT in text:
            seen.append(None)
        return korean_middle_dot(text)

    shown = _map_texts(hypothesis, change)
    return hypothesis if not seen or quotes.has(KATAKANA_MIDDLE_DOT) else shown


def _settle_one(
    hypothesis: HypothesisV3,
    known_hypotheses: set[str],
    known_spans: set[str],
    problem: str = "",
    quotes: Quotes | None = None,
) -> _Settled:
    quotes = quotes or Quotes(problem, list)
    hypothesis = _with_korean_dots(hypothesis, problem, quotes)
    dropped: list[DroppedExpectation] = []
    tests: list[DiscriminatingTestV3] = []
    for test in hypothesis.discriminating_tests:
        kept: list[ExpectedResult] = []
        for row in test.expected_by_hypothesis:
            reason = (
                "UNKNOWN_HYPOTHESIS"
                if row.hypothesis_id not in known_hypotheses
                else "DUPLICATE_HYPOTHESIS"
                if any(item.hypothesis_id == row.hypothesis_id for item in kept)
                else "UNKNOWN_SPAN"
                if not set(row.basis) <= known_spans
                else None
            )
            wrong_language = language_problem(row.expected, problem)
            if reason is None and wrong_language is not None:
                reason = f"EXPECTED_{wrong_language}_MISMATCH"
            if reason is None:
                kept.append(row)
            else:
                dropped.append(
                    DroppedExpectation(
                        test_id=test.test_id,
                        hypothesis_id=row.hypothesis_id,
                        reason=reason,
                        text=row.expected if reason.startswith("EXPECTED_") else None,
                    )
                )
        tests.append(test.model_copy(update={"expected_by_hypothesis": tuple(kept)}))
    conditions, language_drops = _refutation_language(hypothesis, problem)
    dropped.extend(language_drops)
    visible, visible_records = _visible_text(hypothesis, problem, quotes)
    dropped.extend(visible_records)
    settled = hypothesis.model_copy(
        update={
            "discriminating_tests": tuple(tests),
            "refutation_conditions": conditions,
            **visible,
        }
    )
    return _Settled(settled, tuple(dropped))


def settle_contract(
    portfolio: HypothesisPortfolio, evidence: tuple[EvidenceSpan, ...], problem: str = ""
) -> HypothesisPortfolio:
    """Keep what a v3 output says correctly; v2 is untouched.

    Expected-result rows must name known hypotheses and spans; when the question is Korean, a
    label, refutation condition or readable list item (missing evidence, counterevidence queries,
    predicted observations, assumptions) with no Hangul, or with Chinese characters or kana the
    question and the evidence lack, is dropped too; a single readable text is kept and only
    flagged. Every drop is recorded with its text.
    """
    if not isinstance(portfolio, HypothesisPortfolioV3):
        return portfolio
    known_hypotheses = {item.hypothesis_id for item in portfolio.hypotheses}
    known_spans = {span.span_id for span in evidence}
    quotes = Quotes(problem, lambda: (span.exact_text for span in evidence))
    settled: list[HypothesisV3] = []
    for hypothesis in portfolio.hypotheses:
        found = _settle_one(hypothesis, known_hypotheses, known_spans, problem, quotes)
        found.hypothesis.note_dropped(found.dropped)
        settled.append(found.hypothesis)
    return portfolio.model_copy(update={"hypotheses": tuple(settled)})
