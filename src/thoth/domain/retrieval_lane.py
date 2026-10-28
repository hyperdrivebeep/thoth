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
from dataclasses import dataclass, replace
from typing import Literal

from thoth.domain.behavior_artifact import BehaviorArtifactKind
from thoth.domain.behavior_policy import (
    RetrievalFusionPolicy,
    RetrievalLanePolicy,
    RetrievalPolicy,
)
from thoth.domain.evidence import EvidenceSpan, connected_retrieval_spans
from thoth.domain.glossary import Glossary, GlossaryConfidence, GlossaryDetail
from thoth.domain.glossary_lane import (
    GLOSSARY_LANE_PROVENANCE,
    check_glossary_pin,
    expansion_terms,
    glossary_detail,
)
from thoth.domain.lane_scoring import (
    BIGRAM_VERSION,
    BM25_VERSION,
    bigram_terms,
    bm25_scores,
    identifier_terms,
)
from thoth.domain.query_identifiers import (
    SCANNER_VERSION,
    QueryIdentifier,
    fold,
    occurs,
    scan_query,
)
from thoth.domain.retrieval_fusion import (
    FUSION_METHOD,
    FUSION_VERSION,
    Chunk,
    LaneContribution,
    RankedLane,
    admit_chunks,
    lane_contributions,
    merge_round_robin,
)
from thoth.domain.retrieval_terms import KOREAN_FUNCTION_WORDS as KOREAN_FUNCTION_WORDS
from thoth.domain.retrieval_terms import KOREAN_PAIR as KOREAN_PAIR
from thoth.domain.retrieval_terms import MIN_TERM as MIN_TERM
from thoth.domain.structure_groups import GROUPING_VERSION, CatalogGroup
from thoth.domain.structure_groups import block_key as pointer_block_key

LEXICAL_SUBSTRING_V1 = "LEXICAL_SUBSTRING_V1"
PROTECTED_EXACT = "PROTECTED_EXACT"
GROUPED_BM25 = "GROUPED_BM25"
CHAR_BIGRAM = "CHAR_BIGRAM"
GLOSSARY_BM25 = "GLOSSARY_BM25"
# The algorithm that evaluates a retrieval policy. It is recorded in every evaluation output and
# is part of the identity of a retrieval comparison, so a result made by another algorithm is
# never taken for this one.
RETRIEVAL_EVALUATOR_ID = "retrieval-lane-dispatch:1.0.0"


def evaluation_algorithm(component: BehaviorArtifactKind) -> str | None:
    """The evaluation algorithm id a component's comparisons must record, if it has one."""
    return RETRIEVAL_EVALUATOR_ID if component == BehaviorArtifactKind.RETRIEVAL_POLICY else None


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
    # Structure groups of the sources the request may read; only GROUPED_BM25 reads them.
    groups: tuple[CatalogGroup, ...] = ()


@dataclass(frozen=True)
class RetrievalRequest:
    question: str
    queries: tuple[str, ...]
    evidence: tuple[EvidenceSpan, ...]
    pinned_refs: tuple[str, ...] = ()
    catalog: CatalogContext | None = None
    # The Korean-to-English term list, for the glossary lane only. The frame checks it against the
    # digest the policy names before any lane runs and hands the lane the policy's confidence floor.
    glossary: Glossary | None = None
    glossary_min_confidence: GlossaryConfidence | None = None


@dataclass(frozen=True)
class LaneSelection:
    selected: tuple[EvidenceSpan, ...]
    mode: Mode
    eligible: tuple[EvidenceSpan, ...]
    # Why the whole-catalog mode was refused when that is more than "not applicable".
    limitations: tuple[str, ...] = ()
    # Which lane version (and which rule set under it) ranked this selection; empty for the
    # original lane, whose records are unchanged.
    provenance: tuple[tuple[str, str], ...] = ()
    # Per lane, what a fused (4.0.0) selection took from it; empty for a single lane.
    contributions: tuple[LaneContribution, ...] = ()
    # What the glossary lane found and held back, when a fused policy has one and it ran.
    glossary: GlossaryDetail | None = None


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
    return pointer_block_key(span.locator.json_pointer)


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
    policy: RetrievalPolicy,
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


def _hits(text: str, identifiers: tuple[QueryIdentifier, ...]) -> int:
    """Specificity-weighted identifier matches: the written form counts 2, a shorter form 1."""
    score = 0
    for identifier in identifiers:
        for rank, needle in enumerate(identifier.needles):
            if occurs(text, needle, identifier.kind):
                score += 2 if rank == 0 else 1
                break
    return score


def protected_exact_v1(
    request: RetrievalRequest, eligible: tuple[EvidenceSpan, ...]
) -> tuple[EvidenceSpan, ...]:
    """Spans that hold the question's identifiers, found by grammar and matched at identifier edges.

    The question is scanned before anything is split; the Latin and digit pieces that remain are
    matched at the same edges, and Hangul pieces by substring under the earlier two-syllable rule.
    A span with no match is not returned: this lane is for exactness and is meant to be combined.
    """
    fields = [scan_query(text) for text in (request.question, *request.queries)]
    identifiers = tuple({(i.kind, i.needles): i for f in fields for i in f.identifiers}.values())
    latin = tuple(
        dict.fromkeys(
            fold(token.text)
            for f in fields
            for token in f.split_tokens
            if token.script != "HANGUL" and len(token.text) >= MIN_TERM
        )
    )
    hangul = tuple(
        dict.fromkeys(
            fold(token.text)
            for f in fields
            for token in f.split_tokens
            if token.script == "HANGUL"
            and (
                len(token.text) >= MIN_TERM
                or (KOREAN_PAIR.fullmatch(token.text) and token.text not in KOREAN_FUNCTION_WORDS)
            )
        )
    )
    pinned = set(request.pinned_refs)
    exact = {
        s.span_id
        for s in eligible
        if s.span_id in request.question or s.artifact_id in request.question
    }
    scored: list[tuple[tuple[object, ...], EvidenceSpan]] = []
    for span in eligible:
        if span.span_id in pinned:
            continue
        folded = _folded(span.exact_text)
        found = (
            _hits(folded, identifiers),
            sum(1 for word in latin if occurs(folded, word, "MIXED")),
            sum(1 for word in hangul if word in folded),
        )
        if not any(found) and span.span_id not in exact:
            continue
        key = (
            span.span_id not in exact,
            *(-count for count in found),
            span.artifact_id,
            span.locator.page or 0,
            span.locator.line or 0,
            span.span_id,
        )
        scored.append((key, span))
    scored.sort(key=lambda item: item[0])
    return tuple(_interleave_artifacts([span for _, span in scored]))


def _position(span: EvidenceSpan) -> tuple[object, ...]:
    return (span.artifact_id, span.locator.page or 0, span.locator.line or 0, span.span_id)


def _ranked_chunks(
    units: list[tuple[tuple[EvidenceSpan, ...], tuple[EvidenceSpan, ...]]],
    docs: list[list[str]],
    terms: list[str],
    pinned: set[str],
) -> tuple[Chunk, ...]:
    """Units best first: score, then where the unit starts, then its number. Each unit is one chunk:
    its title spans and then its body spans, minus any span already given or pinned."""
    scores = bm25_scores(docs, terms)
    order = sorted(
        (index for index, score in enumerate(scores) if score > 0),
        key=lambda i: (-scores[i], _position(units[i][1][0]), i),
    )
    given: set[str] = set(pinned)
    out: list[Chunk] = []
    for index in order:
        title, body = units[index]
        chunk: list[EvidenceSpan] = []
        for span in (*title, *body):
            if span.span_id not in given:
                given.add(span.span_id)
                chunk.append(span)
        if chunk:
            out.append(tuple(chunk))
    return tuple(out)


def _flat(chunks: tuple[Chunk, ...]) -> tuple[EvidenceSpan, ...]:
    return tuple(span for chunk in chunks for span in chunk)


def _grouped_chunks(
    request: RetrievalRequest, eligible: tuple[EvidenceSpan, ...], terms: list[str]
) -> tuple[Chunk, ...]:
    """Structure groups ranked by BM25 over their title and body for the given query terms."""
    by_id = {s.span_id: s for s in eligible}
    groups = () if request.catalog is None else request.catalog.groups
    units: list[tuple[tuple[EvidenceSpan, ...], tuple[EvidenceSpan, ...]]] = []
    taken: set[str] = set()
    for group in groups:
        body = tuple(by_id[r] for r in group.body_refs if r in by_id and r not in taken)
        if not body:
            continue
        taken.update(s.span_id for s in body)
        title = tuple(by_id[r] for r in group.title_refs if r in by_id)
        units.append((title, body))
    units.extend(((), (s,)) for s in eligible if s.span_id not in taken)
    docs = [
        [
            term
            for span in {s.span_id: s for s in (*title, *body)}.values()
            for term in identifier_terms(span.exact_text)
        ]
        for title, body in units
    ]
    return _ranked_chunks(units, docs, terms, set(request.pinned_refs))


def grouped_bm25_chunks_v1(
    request: RetrievalRequest, eligible: tuple[EvidenceSpan, ...]
) -> tuple[Chunk, ...]:
    """Structure groups ranked by BM25 over their title and body; a span with no group is a unit.

    N and the document frequencies are those of the units of this request's eligible spans. A
    group scores with the title above it, so a row of numbers is found through its version title,
    table head or heading. Spans come back in the order title first, then body in document order.
    """
    terms = [t for text in (request.question, *request.queries) for t in identifier_terms(text)]
    return _grouped_chunks(request, eligible, terms)


def glossary_texts(request: RetrievalRequest) -> tuple[str, ...]:
    return (request.question, *request.queries)


def glossary_bm25_chunks_v1(
    request: RetrievalRequest, eligible: tuple[EvidenceSpan, ...]
) -> tuple[Chunk, ...]:
    """Structure groups ranked by the English terms the question's Korean terms stand for.

    The query is the expansion alone; the question itself is not part of it, so this lane adds
    what the other lanes cannot find and can be told apart from them. With no term to expand, or
    only ambiguous ones, the lane gives nothing.
    """
    terms = expansion_terms(
        glossary_texts(request), request.glossary, request.glossary_min_confidence
    )
    return _grouped_chunks(request, eligible, terms) if terms else ()


def glossary_bm25_v1(
    request: RetrievalRequest, eligible: tuple[EvidenceSpan, ...]
) -> tuple[EvidenceSpan, ...]:
    return _flat(glossary_bm25_chunks_v1(request, eligible))


def grouped_bm25_v1(
    request: RetrievalRequest, eligible: tuple[EvidenceSpan, ...]
) -> tuple[EvidenceSpan, ...]:
    """The same ranking as one list of spans: each group's title first, then its body."""
    return _flat(grouped_bm25_chunks_v1(request, eligible))


def char_bigram_v1(
    request: RetrievalRequest, eligible: tuple[EvidenceSpan, ...]
) -> tuple[EvidenceSpan, ...]:
    """Single spans ranked by BM25 over Hangul syllable bigrams and symbol-preserving words."""
    units = [((), (s,)) for s in eligible]
    docs = [list(bigram_terms(s.exact_text)) for s in eligible]
    terms = [t for text in (request.question, *request.queries) for t in bigram_terms(text)]
    return _flat(_ranked_chunks(units, docs, terms, set(request.pinned_refs)))


LANES: dict[str, Lane] = {}
# A lane whose natural unit is bigger than one span also registers its ranking as chunks; the other
# lanes are ranked chunks of one span each. A fused policy counts depth in these chunks.
type ChunkLane = Callable[[RetrievalRequest, tuple[EvidenceSpan, ...]], tuple[Chunk, ...]]
CHUNK_LANES: dict[str, ChunkLane] = {}
# A lane is its id and its version together: a policy names both, and a lane whose rules change
# is registered under a new version, so the same policy payload can never select differently.
LANE_VERSIONS: dict[str, str] = {}
LANE_PROVENANCE: dict[str, tuple[tuple[str, str], ...]] = {}


def register_lane(
    lane_id: str,
    version: str,
    lane: Lane,
    provenance: tuple[tuple[str, str], ...] = (),
    chunks: ChunkLane | None = None,
) -> None:
    LANES[lane_id] = lane
    LANE_VERSIONS[lane_id] = version
    LANE_PROVENANCE[lane_id] = provenance
    if chunks is None:
        CHUNK_LANES.pop(lane_id, None)
    else:
        CHUNK_LANES[lane_id] = chunks


def lane_chunks(
    lane_id: str, request: RetrievalRequest, eligible: tuple[EvidenceSpan, ...]
) -> tuple[Chunk, ...]:
    chunked = CHUNK_LANES.get(lane_id)
    if chunked is not None:
        return chunked(request, eligible)
    return tuple((span,) for span in LANES[lane_id](request, eligible))


register_lane(LEXICAL_SUBSTRING_V1, "1.0.0", lexical_substring_v1)
register_lane(PROTECTED_EXACT, "1.0.0", protected_exact_v1, (("scanner", SCANNER_VERSION),))
register_lane(
    GROUPED_BM25,
    "1.0.0",
    grouped_bm25_v1,
    (("grouping", GROUPING_VERSION), ("scoring", BM25_VERSION), ("scanner", SCANNER_VERSION)),
    grouped_bm25_chunks_v1,
)
register_lane(
    CHAR_BIGRAM, "1.0.0", char_bigram_v1, (("scoring", BM25_VERSION), ("terms", BIGRAM_VERSION))
)
register_lane(
    GLOSSARY_BM25, "1.0.0", glossary_bm25_v1, GLOSSARY_LANE_PROVENANCE, glossary_bm25_chunks_v1
)


def select_retrieval_context(
    request: RetrievalRequest,
    policy: RetrievalPolicy,
    lane_id: str | None = None,
) -> LaneSelection:
    if isinstance(policy, RetrievalFusionPolicy):
        if lane_id is not None:
            raise ValueError("RETRIEVAL_LANE_ID_NOT_ALLOWED_FOR_FUSION")
        return _select_fused(request, policy)
    if lane_id is None:
        lane_id = (
            policy.lanes[0].lane_id
            if isinstance(policy, RetrievalLanePolicy)
            else LEXICAL_SUBSTRING_V1
        )
    lane = LANES.get(lane_id)
    if lane is None:
        raise ValueError("RETRIEVAL_LANE_UNKNOWN")
    named = policy.lanes[0] if isinstance(policy, RetrievalLanePolicy) else None
    if (
        named is not None
        and named.lane_id == lane_id
        and LANE_VERSIONS.get(lane_id) != named.lane_version
    ):
        raise ValueError("RETRIEVAL_LANE_VERSION_UNKNOWN")
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
    extra = LANE_PROVENANCE.get(lane_id, ())
    version = LANE_VERSIONS.get(lane_id, "")
    provenance = (("lane_id", lane_id), ("lane_version", version), *extra)
    return LaneSelection(
        tuple(selected),
        "LEXICAL" if observed else "LEGACY",
        eligible,
        limitations,
        provenance if extra else (),
    )


def _select_fused(request: RetrievalRequest, policy: RetrievalFusionPolicy) -> LaneSelection:
    """Policy 4.0.0: each lane ranks the same eligible spans, then the merge and the frame admit."""
    for spec in policy.lanes:
        if spec.lane_id not in LANES:
            raise ValueError("RETRIEVAL_LANE_UNKNOWN")
        if LANE_VERSIONS.get(spec.lane_id) != spec.lane_version:
            raise ValueError("RETRIEVAL_LANE_VERSION_UNKNOWN")
        check_glossary_pin(spec, request.glossary)
    eligible = connected_retrieval_spans(request.evidence)
    complete, limitations = full_catalog(eligible, request.catalog, policy)
    if complete is not None:
        return LaneSelection(complete, "FULL_ELIGIBLE_EXTRACTED_CATALOG", eligible, limitations)
    pinned = set(request.pinned_refs)
    selected = [span for span in eligible if span.span_id in pinned][: policy.max_spans]
    ranked = [
        RankedLane(
            spec.lane_id,
            spec.lane_version,
            spec.depth,
            lane_chunks(
                spec.lane_id,
                request
                if spec.glossary is None
                else replace(request, glossary_min_confidence=spec.glossary.min_confidence),
                eligible,
            ),
        )
        for spec in policy.lanes
    ]
    credited = admit_chunks(
        merge_round_robin(ranked), selected, policy.max_spans, policy.character_budget
    )
    provenance: list[tuple[str, str]] = [
        ("fusion", FUSION_METHOD),
        ("fusion_version", FUSION_VERSION),
    ]
    for index, lane in enumerate(ranked):
        provenance.append(
            (f"lane{index}", f"{lane.lane_id}:{lane.lane_version}:depth={lane.depth}")
        )
        provenance.extend(
            (f"lane{index}.{key}", value) for key, value in LANE_PROVENANCE.get(lane.lane_id, ())
        )
    detail, glossary_keys = glossary_detail(policy.lanes, request.glossary, glossary_texts(request))
    provenance.extend(glossary_keys)
    observed = request.catalog is not None and request.catalog.all_observed
    return LaneSelection(
        tuple(selected),
        "LEXICAL" if observed else "LEGACY",
        eligible,
        limitations,
        tuple(provenance),
        lane_contributions(ranked, selected, credited),
        detail,
    )
