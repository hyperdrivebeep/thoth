"""Flag new Hangul prose in an English request; preserve quotations, names and stored text."""

import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import cast

from pydantic import BaseModel

from thoth.application.services.text_script import Quotes
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.hypothesis import Hypothesis, HypothesisPortfolio, HypothesisV3
from thoth.domain.language_validation import HypothesisLanguageCheck
from thoth.domain.model import ContextPack
from thoth.domain.research_execution import research_work
from thoth.domain.research_projection import DroppedExpectation

_HANGUL = re.compile(r"[\u1100-\u11ff\u3130-\u318f\ua960-\ua97f\uac00-\ud7ff]")
_QUOTES = tuple(
    re.compile(pattern)
    for pattern in (
        r"```(?:[\w+-]+\s*\n)?([\s\S]*?)```",
        r"`([^`]+)`",
        r'"([^"\n]+)"',
        r"'([^'\n]+)'",
        r"“([^”]+)”",
        r"\u2018([^\u2019]+)\u2019",
        r"「([^」]+)」",
        r"『([^』]+)』",
    )
)
_SOURCE_CONTEXT = ("source_overview", "source_packet", "connected_sources", "context_bundle")
_NAME_KEYS = frozenset({"title", "name", "filename", "file_name", "source_uri", "label"})


def _strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, BaseModel):
        yield from _strings(value.model_dump())
    elif isinstance(value, dict):
        for item in cast(dict[str, object], value).values():
            yield from _strings(item)
    elif isinstance(value, (tuple, list)):
        for item in cast(Sequence[object], value):
            yield from _strings(item)


def _names(value: object) -> Iterator[str]:
    if isinstance(value, BaseModel):
        yield from _names(value.model_dump())
    elif isinstance(value, dict):
        for key, item in cast(dict[str, object], value).items():
            if key in _NAME_KEYS and isinstance(item, str):
                yield item
            else:
                yield from _names(item)
    elif isinstance(value, (tuple, list)):
        for item in cast(Sequence[object], value):
            yield from _names(item)


@dataclass(frozen=True)
class EnglishProseCheck:
    quotes: Quotes
    verbatim: frozenset[str]
    names: tuple[str, ...]

    @classmethod
    def build(
        cls,
        problem: str,
        evidence: tuple[EvidenceSpan, ...],
        identifiers: Iterable[str] = (),
        context: ContextPack | None = None,
    ) -> "EnglishProseCheck":
        originals = [problem, *(span.exact_text for span in evidence)]
        names = set(identifiers)
        for span in evidence:
            names.update((span.span_id, span.artifact_id, span.source_version_id, span.project_id))
            names.update(_strings(span.locator))
        if context is not None:
            names.update((context.project_id, context.object_id, context.case_id))
            stored = tuple(
                _strings(
                    (
                        context.previous_portfolio,
                        context.canonical_hypotheses,
                        context.canonical_portfolio,
                    )
                )
            )
            originals.extend(stored)
            names.update(stored)
            for key in _SOURCE_CONTEXT:
                value = context.research_context.get(key)
                originals.extend(_strings(value))
                names.update(_names(value))
        originals.extend(names)
        return cls(
            Quotes(problem, lambda: originals),
            frozenset(s.strip() for s in originals if s),
            tuple(sorted((s for s in names if s and _HANGUL.search(s)), key=len, reverse=True)),
        )

    def mismatch(self, text: str | None) -> bool:
        if not text or text.strip() in self.verbatim:
            return False
        remainder = text
        for pattern in _QUOTES:
            remainder = pattern.sub(
                lambda match: "" if self.quotes.has(match[1].strip()) else match[0],
                remainder,
            )
        for name in self.names:
            remainder = remainder.replace(name, "")
        return _HANGUL.search(remainder) is not None


def hypothesis_english_flags(
    hypothesis: Hypothesis,
    check: EnglishProseCheck,
) -> tuple[DroppedExpectation, ...]:
    flags: list[DroppedExpectation] = []

    def inspect(field: str, text: str | None, test_id: str = "", target: str | None = None) -> None:
        if check.mismatch(text):
            flags.append(
                DroppedExpectation(
                    test_id=test_id,
                    hypothesis_id=target or hypothesis.hypothesis_id,
                    reason=f"{field}_LANGUAGE_FLAGGED",
                    text=text,
                )
            )

    for field in ("statement", "uncertainty", "observed_problem"):
        inspect(field.upper(), getattr(hypothesis, field))
    for field in (
        "missing_evidence",
        "counterevidence_queries",
        "predicted_observations",
        "assumptions",
    ):
        for text in getattr(hypothesis, field):
            inspect(field.upper(), text)
    for test in hypothesis.discriminating_tests:
        for field in ("procedure_candidate", "expected_if_true", "expected_if_alternative"):
            inspect(field.upper(), getattr(test, field), test.test_id)
    if isinstance(hypothesis, HypothesisV3):
        for text in hypothesis.refutation_conditions:
            inspect("REFUTATION_CONDITIONS", text)
        for test in hypothesis.discriminating_tests:
            for row in test.expected_by_hypothesis:
                inspect("EXPECTED", row.expected, test.test_id, row.hypothesis_id)
    return tuple(flags)


def record_english_check(portfolio: HypothesisPortfolio, context: ContextPack) -> None:
    work = research_work.get()
    if work is None or work.response_language != "en":
        return
    identifiers = (
        portfolio.object_id,
        *(h.hypothesis_id for h in portfolio.hypotheses),
        *(t.test_id for h in portfolio.hypotheses for t in h.discriminating_tests),
    )
    check = EnglishProseCheck.build(context.problem, context.evidence, identifiers, context)
    flags: list[DroppedExpectation] = []
    for hypothesis in portfolio.hypotheses:
        flags.extend(hypothesis_english_flags(hypothesis, check))
        if isinstance(hypothesis, HypothesisV3):
            flags.extend(
                item
                for item in cast(tuple[DroppedExpectation, ...], hypothesis.dropped)
                if item.reason.endswith("_LANGUAGE_FLAGGED")
            )
    unique = tuple(dict.fromkeys(flags))
    work.result_only["hypothesis_language_check"] = HypothesisLanguageCheck(
        flagged_items=len(unique),
        hypotheses_checked=len(portfolio.hypotheses),
        findings=unique,
    ).model_dump(mode="json")
