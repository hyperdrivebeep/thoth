"""Lexical candidates preserve exact identifiers and neighboring original spans."""

import re
import unicodedata
from collections import defaultdict, deque
from time import perf_counter_ns

from thoth.application.services.research_retrieval_policy import (
    record_selection,
    replay_retrieval_shadows,
    retrieval_policy,
)
from thoth.domain.evidence import EvidenceSpan, connected_retrieval_spans

_MIN_TERM = 3
_MIN_SPAN_CHARS = 24


def tokens(text: str) -> set[str]:
    return set(re.findall(r"[\w]+(?:[-./][\w]+)*", unicodedata.normalize("NFKC", text).casefold()))


def _folded(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _term_hits(text: str, terms: set[str]) -> int:
    folded = _folded(text)
    return sum(1 for term in terms if term in folded)


def _is_chrome(span: EvidenceSpan) -> bool:
    text = span.exact_text.strip()
    if len(text) < _MIN_SPAN_CHARS:
        return True
    lowered = text.casefold()
    if lowered in {"---", "article"}:
        return True
    return lowered.startswith("*   [](http") or "sharer.php" in lowered or "intent/tweet" in lowered


def _rank_key(span: EvidenceSpan, *, exact: set[str], terms: set[str]) -> tuple[object, ...]:
    return (
        span.span_id not in exact,
        -_term_hits(span.exact_text, terms),
        -len(span.exact_text),
        span.artifact_id,
        span.locator.page or 0,
        span.locator.line or 0,
        span.span_id,
    )


def _interleave_artifacts(ranked: list[EvidenceSpan]) -> list[EvidenceSpan]:
    buckets: dict[str, deque[EvidenceSpan]] = defaultdict(deque)
    order: list[str] = []
    for span in ranked:
        if span.artifact_id not in buckets:
            order.append(span.artifact_id)
        buckets[span.artifact_id].append(span)
    interleaved: list[EvidenceSpan] = []
    while any(buckets[artifact_id] for artifact_id in order):
        for artifact_id in order:
            if buckets[artifact_id]:
                interleaved.append(buckets[artifact_id].popleft())
    return interleaved


def text_neighbors(
    span: EvidenceSpan, source: tuple[EvidenceSpan, ...]
) -> tuple[EvidenceSpan, ...]:
    allowed = {item.span_id for item in connected_retrieval_spans(source)}
    siblings = sorted(
        (
            s
            for s in source
            if s.artifact_id == span.artifact_id
            and s.source_version_id == span.source_version_id
            and s.span_id in allowed
        ),
        key=lambda s: (s.locator.page or 0, s.locator.line or 0, s.span_id),
    )
    index = next(i for i, s in enumerate(siblings) if s.span_id == span.span_id)
    return tuple([*siblings[:1], *siblings[max(0, index - 1) : index + 2]])


def lexical_candidates(
    question: str, queries: tuple[str, ...], evidence: tuple[EvidenceSpan, ...]
) -> tuple[EvidenceSpan, ...]:
    policy, started = retrieval_policy(), perf_counter_ns()
    terms = {token for token in tokens(" ".join((question, *queries))) if len(token) >= _MIN_TERM}
    eligible = connected_retrieval_spans(evidence)
    pool = tuple(span for span in eligible if not _is_chrome(span)) or eligible
    exact = {s.span_id for s in pool if s.span_id in question or s.artifact_id in question}
    ranked = sorted(pool, key=lambda span: _rank_key(span, exact=exact, terms=terms))
    if any(span.span_id in exact or _term_hits(span.exact_text, terms) for span in ranked):
        ranked = [
            span
            for span in ranked
            if span.span_id in exact or _term_hits(span.exact_text, terms)
        ]
    # A bounded shortlist, not a claim that unselected source territory was searched.
    selected: list[EvidenceSpan] = []
    size = 0
    for span in _interleave_artifacts(ranked):
        if (
            len(selected) >= policy.max_spans
            or size + len(span.exact_text) > policy.character_budget
        ):
            continue
        selected.append(span)
        size += len(span.exact_text)
    result = tuple(selected)
    record_selection(
        "LEXICAL_CANDIDATES",
        {"question": question, "queries": queries, "evidence": evidence},
        result,
        started,
    )
    replay_retrieval_shadows(lambda: lexical_candidates(question, queries, evidence))
    return result


def adjacent_packet(
    ranking: tuple[str, ...],
    candidates: tuple[EvidenceSpan, ...],
    source: tuple[EvidenceSpan, ...],
    question: str,
) -> tuple[EvidenceSpan, ...]:
    policy, started = retrieval_policy(), perf_counter_ns()
    allowed = {s.span_id: s for s in candidates}
    if set(ranking) - allowed.keys() or len(set(ranking)) != len(ranking):
        raise ValueError("RERANK_UNKNOWN_OR_DUPLICATE_ID")
    ordered = list(
        dict.fromkeys(
            (
                *(
                    s.span_id
                    for s in candidates
                    if s.span_id in question or s.artifact_id in question
                ),
                *ranking,
            )
        )
    )
    selected: list[EvidenceSpan] = []
    seen: set[str] = set()
    size = 0
    for identifier in ordered:
        span = allowed[identifier]
        # Keep each chunk with preceding/following text, and the first header chunk.
        group = text_neighbors(span, source)
        needed = list({s.span_id: s for s in group if s.span_id not in seen}.values())
        cost = sum(len(s.exact_text) for s in needed)
        if size + cost > policy.character_budget or len(selected) + len(needed) > policy.max_spans:
            continue
        selected.extend(needed)
        seen.update(s.span_id for s in needed)
        size += cost
    result = tuple(selected)
    record_selection("CONTEXT_PACKET", {"ranking": ranking, "source": source}, result, started)
    replay_retrieval_shadows(lambda: adjacent_packet(ranking, candidates, source, question))
    return result
