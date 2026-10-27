"""One pure function selects the research candidate shortlist; lanes only rank.

The frame owns what every lane shares: which spans may be read at all, the whole-catalog mode, the
spans a request pinned, and the span and character budget. A lane is a named ranking rule looked up
in LANES. LEXICAL_SUBSTRING_V1 is the rule the research path has always used, moved here without
change so the research path, shadow replay and behavior evaluation can call the same function.
"""

import re
import unicodedata
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from thoth.domain.behavior_policy import RetrievalBehaviorPolicy
from thoth.domain.evidence import EvidenceSpan, connected_retrieval_spans

LEXICAL_SUBSTRING_V1 = "LEXICAL_SUBSTRING_V1"

MIN_TERM = 3
KOREAN_PAIR = re.compile(r"[가-힣]{2}")
# Only the newly admitted two-syllable words are filtered. These grammatical/context
# words add little topic information; no domain words or scenario-specific exceptions.
KOREAN_FUNCTION_WORDS = frozenset(
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
MIN_SPAN_CHARS = 24
# A block of short key-value lines is read together only while it stays small.
MAX_BLOCK_SPANS = 20

type Mode = Literal["FULL_ELIGIBLE_EXTRACTED_CATALOG", "LEXICAL", "LEGACY"]


@dataclass(frozen=True)
class CatalogContext:
    """What the structure observation says about the spans a request may read."""

    ordered_refs: tuple[str, ...]
    protected_refs: frozenset[str]
    all_observed: bool
    structure_count: int


@dataclass(frozen=True)
class RetrievalRequest:
    question: str
    queries: tuple[str, ...]
    evidence: tuple[EvidenceSpan, ...]
    pinned_refs: tuple[str, ...] = ()
    catalog: CatalogContext | None = None


@dataclass(frozen=True)
class LaneSelection:
    selected: tuple[EvidenceSpan, ...]
    mode: Mode
    eligible: tuple[EvidenceSpan, ...]
    # Why the whole-catalog mode was refused when that is more than "not applicable".
    limitations: tuple[str, ...] = ()


type Lane = Callable[[RetrievalRequest, tuple[EvidenceSpan, ...]], tuple[EvidenceSpan, ...]]


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
        if len(token) >= MIN_TERM
        or (KOREAN_PAIR.fullmatch(token) and token not in KOREAN_FUNCTION_WORDS)
    }


def _folded(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _term_hits(text: str, terms: set[str]) -> int:
    folded = _folded(text)
    return sum(1 for term in terms if term in folded)


def block_key(span: EvidenceSpan) -> str | None:
    """The key path that groups a structured line with the lines under the same parent key."""
    pointer = span.locator.json_pointer
    if not pointer or not pointer.startswith("/"):
        return None
    parent, _, _ = pointer.rpartition("/")
    return parent or pointer


def _is_chrome(span: EvidenceSpan) -> bool:
    text = span.exact_text.strip()
    if len(text) < MIN_SPAN_CHARS and not block_key(span):
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


def full_catalog(
    eligible: tuple[EvidenceSpan, ...],
    catalog: CatalogContext | None,
    policy: RetrievalBehaviorPolicy,
) -> tuple[tuple[EvidenceSpan, ...] | None, tuple[str, ...]]:
    """One admission decision for selection and its coverage mode; None means fallback."""
    if catalog is None or not catalog.all_observed:
        return None, ()
    by_id = {s.span_id: s for s in eligible}
    if (
        set(catalog.ordered_refs) != set(by_id)
        or len(catalog.ordered_refs) != len(by_id)
        or len(eligible) != len(by_id)
    ):
        return None, ("CONTEXT_ORDERED_REFS_MISMATCH",)
    if (
        len(eligible) > policy.max_spans
        or catalog.structure_count > policy.max_spans
        or sum(len(s.exact_text) for s in eligible) > policy.character_budget
    ):
        return None, ()
    return tuple(by_id[k] for k in catalog.ordered_refs), ()


def lexical_substring_v1(
    request: RetrievalRequest, eligible: tuple[EvidenceSpan, ...]
) -> tuple[EvidenceSpan, ...]:
    """Candidates ranked by substring term coverage, as the research path has always done."""
    terms = search_terms(" ".join((request.question, *request.queries)))
    protected: frozenset[str] = (
        frozenset() if request.catalog is None else request.catalog.protected_refs
    )
    pool = (
        tuple(span for span in eligible if not _is_chrome(span) or span.span_id in protected)
        or eligible
    )
    exact = {
        s.span_id
        for s in pool
        if s.span_id in request.question or s.artifact_id in request.question
    }
    ranked = sorted(pool, key=lambda span: _rank_key(span, exact=exact, terms=terms))
    if any(span.span_id in exact or _term_hits(span.exact_text, terms) for span in ranked):
        ranked = [
            span for span in ranked if span.span_id in exact or _term_hits(span.exact_text, terms)
        ]
    pinned = set(request.pinned_refs)
    return tuple(_interleave_artifacts([span for span in ranked if span.span_id not in pinned]))


LANES: dict[str, Lane] = {LEXICAL_SUBSTRING_V1: lexical_substring_v1}


def select_retrieval_context(
    request: RetrievalRequest,
    policy: RetrievalBehaviorPolicy,
    lane_id: str = LEXICAL_SUBSTRING_V1,
) -> LaneSelection:
    lane = LANES.get(lane_id)
    if lane is None:
        raise ValueError("RETRIEVAL_LANE_UNKNOWN")
    eligible = connected_retrieval_spans(request.evidence)
    complete, limitations = full_catalog(eligible, request.catalog, policy)
    if complete is not None:
        return LaneSelection(complete, "FULL_ELIGIBLE_EXTRACTED_CATALOG", eligible, limitations)
    pinned = set(request.pinned_refs)
    # A pinned span is kept first when it is retrievable at all; it never widens what is eligible.
    # A bounded shortlist, not a claim that unselected source territory was searched.
    selected = [span for span in eligible if span.span_id in pinned][: policy.max_spans]
    size = sum(len(span.exact_text) for span in selected)
    for span in lane(request, eligible):
        if (
            len(selected) >= policy.max_spans
            or size + len(span.exact_text) > policy.character_budget
        ):
            continue
        selected.append(span)
        size += len(span.exact_text)
    observed = request.catalog is not None and request.catalog.all_observed
    return LaneSelection(
        tuple(selected), "LEXICAL" if observed else "LEGACY", eligible, limitations
    )
