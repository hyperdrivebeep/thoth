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
from thoth.application.services.research_selection_context import (
    CandidateContext,
    candidate_context,
)
from thoth.domain.behavior_policy import RetrievalBehaviorPolicy
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.evidence import EvidenceSpan, connected_retrieval_spans
from thoth.domain.research_execution import ResearchWork
from thoth.domain.retrieval_coverage import RetrievalSelection
from thoth.ports.artifact_ledger import ArtifactLedgerPort

_MIN_TERM = 3
_KOREAN_PAIR = re.compile(r"[가-힣]{2}")
# Only the newly admitted two-syllable words are filtered. These grammatical/context
# words add little topic information; no domain words or scenario-specific exceptions.
_KOREAN_FUNCTION_WORDS = frozenset(
    {
        "경우",
        "관련",
        "때문",
        "또는",
        "대한",
        "대해",
        "따라",
        "또한",
        "위한",
        "위해",
        "이번",
        "이런",
        "저런",
        "통해",
        "에서",
        "으로",
    }
)
_MIN_SPAN_CHARS = 24
# A block of short key-value lines is read together only while it stays small.
_MAX_BLOCK_SPANS = 20


def tokens(text: str) -> set[str]:
    return set(re.findall(r"[\w]+(?:[-./][\w]+)*", unicodedata.normalize("NFKC", text).casefold()))


def search_terms(text: str) -> set[str]:
    """Keep legacy long tokens plus topic-bearing, whole two-syllable Korean tokens.

    Memory recall's n-grams would also change long-token matching. Particle splitting
    is intentionally not applied here, so a longer token still matches as written.
    """
    return {
        token
        for token in tokens(text)
        if len(token) >= _MIN_TERM
        or (_KOREAN_PAIR.fullmatch(token) and token not in _KOREAN_FUNCTION_WORDS)
    }


def _folded(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _term_hits(text: str, terms: set[str]) -> int:
    folded = _folded(text)
    return sum(1 for term in terms if term in folded)


def _is_chrome(span: EvidenceSpan) -> bool:
    text = span.exact_text.strip()
    if len(text) < _MIN_SPAN_CHARS and not _block_key(span):
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


def _block_key(span: EvidenceSpan) -> str | None:
    """The key path that groups a structured line with the lines under the same parent key."""
    pointer = span.locator.json_pointer
    if not pointer or not pointer.startswith("/"):
        return None
    parent, _, _ = pointer.rpartition("/")
    return parent or pointer


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
    near = [*siblings[:1], *siblings[max(0, index - 1) : index + 2]]
    key = _block_key(span)
    if key is None:
        return tuple(near)
    block = [
        s
        for s in siblings
        if (s.locator.json_pointer or "") == key
        or (s.locator.json_pointer or "").startswith(f"{key}/")
    ]
    if len(block) > _MAX_BLOCK_SPANS:
        return tuple(near)
    return tuple(dict.fromkeys([*near, *block]))


def _full_catalog(
    eligible: tuple[EvidenceSpan, ...],
    context: CandidateContext | None,
    policy: RetrievalBehaviorPolicy,
) -> tuple[tuple[EvidenceSpan, ...] | None, tuple[str, ...]]:
    """One admission decision for selection and its coverage mode; None means fallback."""
    if context is None or not context.all_observed:
        return None, ()
    by_id = {s.span_id: s for s in eligible}
    if (
        set(context.ordered_refs) != set(by_id)
        or len(context.ordered_refs) != len(by_id)
        or len(eligible) != len(by_id)
    ):
        return None, ("CONTEXT_ORDERED_REFS_MISMATCH",)
    if (
        len(eligible) > policy.max_spans
        or context.structure_count > policy.max_spans
        or sum(len(s.exact_text) for s in eligible) > policy.character_budget
    ):
        return None, ()
    return tuple(by_id[k] for k in context.ordered_refs), ()


def lexical_candidates(
    question: str,
    queries: tuple[str, ...],
    evidence: tuple[EvidenceSpan, ...],
    pinned: tuple[str, ...] = (),
    *,
    context: CandidateContext | None = None,
) -> tuple[EvidenceSpan, ...]:
    policy, started = retrieval_policy(), perf_counter_ns()
    terms = search_terms(" ".join((question, *queries)))
    eligible = connected_retrieval_spans(evidence)
    complete, _ = _full_catalog(eligible, context, policy)
    if complete is not None:
        result = complete
        record_selection(
            "FULL_ELIGIBLE_EXTRACTED_CATALOG",
            {"question": question, "queries": queries, "evidence": evidence},
            result,
            started,
        )
        replay_retrieval_shadows(
            lambda: lexical_candidates(question, queries, evidence, pinned, context=context)
        )
        return result
    protected = frozenset() if context is None else context.protected_refs
    pool = (
        tuple(span for span in eligible if not _is_chrome(span) or span.span_id in protected)
        or eligible
    )
    exact = {s.span_id for s in pool if s.span_id in question or s.artifact_id in question}
    ranked = sorted(pool, key=lambda span: _rank_key(span, exact=exact, terms=terms))
    if any(span.span_id in exact or _term_hits(span.exact_text, terms) for span in ranked):
        ranked = [
            span for span in ranked if span.span_id in exact or _term_hits(span.exact_text, terms)
        ]
    # A bounded shortlist, not a claim that unselected source territory was searched.
    # A pinned span is kept first when it is retrievable at all; it never widens what is eligible.
    selected = [span for span in eligible if span.span_id in pinned][: policy.max_spans]
    size = sum(len(span.exact_text) for span in selected)
    for span in _interleave_artifacts([span for span in ranked if span.span_id not in pinned]):
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
    replay_retrieval_shadows(
        lambda: lexical_candidates(question, queries, evidence, pinned, context=context)
    )
    return result


def work_candidates(
    work: ResearchWork,
    queries: tuple[str, ...],
    evidence: tuple[EvidenceSpan, ...],
    artifacts: ArtifactLedgerPort | None = None,
) -> tuple[EvidenceSpan, ...]:
    """The shortlist for a request: its question, its queries and the spans its start row pins."""
    eligible = connected_retrieval_spans(evidence)
    context = None if artifacts is None else candidate_context(eligible, artifacts)
    result = lexical_candidates(
        work.effective_question, queries, evidence, work.pinned_spans, context=context
    )
    policy = retrieval_policy()
    complete, limitations = _full_catalog(eligible, context, policy)
    work.retrieval_selection = RetrievalSelection(
        evidence,
        eligible,
        tuple(s.span_id for s in result),
        domain_digest("RETRIEVAL_SOURCE", "1.0.0", canonical_payload({"source": evidence}))
        if context is None
        else context.basis_digest,
        domain_digest("RETRIEVAL_POLICY", "1.0.0", canonical_payload(policy)),
        "FULL_ELIGIBLE_EXTRACTED_CATALOG"
        if complete is not None
        else "LEGACY"
        if context is None or not context.all_observed
        else "LEXICAL",
        ("LEGACY_CONTEXT",) if context is None else (*context.limitations, *limitations),
        contextual_refs=() if context is None else context.ordered_refs,
    )
    return result


def pinned_first(
    ranking: tuple[str, ...], candidates: tuple[EvidenceSpan, ...], pinned: tuple[str, ...]
) -> tuple[str, ...]:
    """The ranking with the pinned candidates the model left out put in front of it.

    A pinned span is the starting row's own evidence, so a model's ordering cannot drop it. An id
    the model repeated is left as it was: the assembly still refuses it.
    """
    left_out = tuple(
        span.span_id
        for span in candidates
        if span.span_id in pinned and span.span_id not in ranking
    )
    return (*left_out, *ranking)


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
