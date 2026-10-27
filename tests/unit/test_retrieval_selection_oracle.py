"""S1 of the single retrieval function: pin what the research path selects today.

The selection rules are copied here, unchanged, from research_retrieval.py at commit 72b1461 and
kept as an oracle. Every scenario must give the same ordered span IDs from the oracle and from the
function the research path calls now, and a fixed digest over all results is pinned. When the rules
move into one shared function, this file is what proves the move changed nothing. Spans and
questions are synthetic.
"""

import hashlib
import random
import re
import unicodedata
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import cast

import pytest
from pydantic import JsonValue

from thoth.adapters.behavior.registry import RetrievalBehaviorHandler
from thoth.application.services import research_retrieval
from thoth.application.services.research_selection_context import CandidateContext
from thoth.domain.artifact import SourceLocator
from thoth.domain.behavior_policy import RetrievalBehaviorPolicy, select_evidence_context
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import (
    AuthorityState,
    CutoffState,
    SupportState,
    VerificationState,
)
from thoth.domain.evidence import EvidenceSpan, connected_retrieval_spans

# ---------------------------------------------------------------------------------------------
# Oracle: the selection rules of lexical_candidates at 72b1461, with the policy as an argument.
# ---------------------------------------------------------------------------------------------
_MIN_TERM = 3
_KOREAN_PAIR = re.compile(r"[가-힣]{2}")
_FUNCTION_WORDS = frozenset(
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


def o_tokens(text: str) -> set[str]:
    return set(re.findall(r"[\w]+(?:[-./][\w]+)*", unicodedata.normalize("NFKC", text).casefold()))


def o_search_terms(text: str) -> set[str]:
    return {
        token
        for token in o_tokens(text)
        if len(token) >= _MIN_TERM
        or (_KOREAN_PAIR.fullmatch(token) and token not in _FUNCTION_WORDS)
    }


def o_folded(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def o_term_hits(text: str, terms: set[str]) -> int:
    folded = o_folded(text)
    return sum(1 for term in terms if term in folded)


def o_block_key(span: EvidenceSpan) -> str | None:
    pointer = span.locator.json_pointer
    if not pointer or not pointer.startswith("/"):
        return None
    parent, _, _ = pointer.rpartition("/")
    return parent or pointer


def o_is_chrome(span: EvidenceSpan) -> bool:
    text = span.exact_text.strip()
    if len(text) < _MIN_SPAN_CHARS and not o_block_key(span):
        return True
    lowered = text.casefold()
    if lowered in {"---", "article"}:
        return True
    return lowered.startswith("*   [](http") or "sharer.php" in lowered or "intent/tweet" in lowered


def o_rank_key(span: EvidenceSpan, *, exact: set[str], terms: set[str]) -> tuple[object, ...]:
    return (
        span.span_id not in exact,
        -o_term_hits(span.exact_text, terms),
        -len(span.exact_text),
        span.artifact_id,
        span.locator.page or 0,
        span.locator.line or 0,
        span.span_id,
    )


def o_interleave(ranked: list[EvidenceSpan]) -> list[EvidenceSpan]:
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


def o_full_catalog(
    eligible: tuple[EvidenceSpan, ...],
    context: CandidateContext | None,
    policy: RetrievalBehaviorPolicy,
) -> tuple[EvidenceSpan, ...] | None:
    if context is None or not context.all_observed:
        return None
    by_id = {s.span_id: s for s in eligible}
    if (
        set(context.ordered_refs) != set(by_id)
        or len(context.ordered_refs) != len(by_id)
        or len(eligible) != len(by_id)
    ):
        return None
    if (
        len(eligible) > policy.max_spans
        or context.structure_count > policy.max_spans
        or sum(len(s.exact_text) for s in eligible) > policy.character_budget
    ):
        return None
    return tuple(by_id[k] for k in context.ordered_refs)


def oracle_select(
    question: str,
    queries: tuple[str, ...],
    evidence: tuple[EvidenceSpan, ...],
    pinned: tuple[str, ...],
    context: CandidateContext | None,
    policy: RetrievalBehaviorPolicy,
) -> list[str]:
    terms = o_search_terms(" ".join((question, *queries)))
    eligible = connected_retrieval_spans(evidence)
    complete = o_full_catalog(eligible, context, policy)
    if complete is not None:
        return [s.span_id for s in complete]
    protected: frozenset[str] = frozenset() if context is None else context.protected_refs
    pool = (
        tuple(span for span in eligible if not o_is_chrome(span) or span.span_id in protected)
        or eligible
    )
    exact = {s.span_id for s in pool if s.span_id in question or s.artifact_id in question}
    ranked = sorted(pool, key=lambda span: o_rank_key(span, exact=exact, terms=terms))
    if any(span.span_id in exact or o_term_hits(span.exact_text, terms) for span in ranked):
        ranked = [
            span for span in ranked if span.span_id in exact or o_term_hits(span.exact_text, terms)
        ]
    selected = [span for span in eligible if span.span_id in pinned][: policy.max_spans]
    size = sum(len(span.exact_text) for span in selected)
    for span in o_interleave([span for span in ranked if span.span_id not in pinned]):
        if (
            len(selected) >= policy.max_spans
            or size + len(span.exact_text) > policy.character_budget
        ):
            continue
        selected.append(span)
        size += len(span.exact_text)
    return [s.span_id for s in selected]


# ---------------------------------------------------------------------------------------------
# The function the research path calls today. The one place to repoint when the rules move.
# ---------------------------------------------------------------------------------------------
def current_select(
    question: str,
    queries: tuple[str, ...],
    evidence: tuple[EvidenceSpan, ...],
    pinned: tuple[str, ...],
    context: CandidateContext | None,
    policy: RetrievalBehaviorPolicy,
) -> list[str]:
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(research_retrieval, "retrieval_policy", lambda: policy)
        selected = research_retrieval.lexical_candidates(
            question, queries, evidence, pinned, context=context
        )
    return [s.span_id for s in selected]


# ---------------------------------------------------------------------------------------------
# Synthetic fixtures
# ---------------------------------------------------------------------------------------------
def span(
    name: str,
    text: str,
    *,
    artifact: str = "artifact:a",
    line: int | None = None,
    page: int | None = None,
    pointer: str | None = None,
    authority: AuthorityState = AuthorityState.INFORMAL,
    cutoff: CutoffState = CutoffState.ELIGIBLE,
) -> EvidenceSpan:
    return EvidenceSpan(
        span_id=f"span:{name}",
        project_id="project:s1",
        artifact_id=artifact,
        source_version_id=f"version:{artifact}",
        locator=SourceLocator(line=line, page=page, json_pointer=pointer),
        exact_text=text,
        text_sha256=hashlib.sha256(text.encode()).hexdigest(),
        extraction_method="fixture",
        support_state=SupportState.EXTRACTED,
        authority_state=authority,
        verification_state=VerificationState.SCHEMA_VALID,
        cutoff_state=cutoff,
    )


def observed(
    spans: tuple[EvidenceSpan, ...],
    *,
    order: tuple[str, ...] | None = None,
    protected: frozenset[str] = frozenset(),
    all_observed: bool = True,
    structure_count: int = 0,
) -> CandidateContext:
    refs = order if order is not None else tuple(s.span_id for s in spans)
    return CandidateContext(refs, protected, all_observed, structure_count, "0" * 64, ())


@dataclass(frozen=True)
class Scenario:
    question: str
    evidence: tuple[EvidenceSpan, ...]
    queries: tuple[str, ...] = ()
    pinned: tuple[str, ...] = ()
    context: CandidateContext | None = None
    policy: RetrievalBehaviorPolicy = field(default_factory=RetrievalBehaviorPolicy)


def measurement_spans() -> tuple[EvidenceSpan, ...]:
    return (
        span("title143", "Polars 1.43.2", line=1),
        span("m143", "fill_nan 10000 cols 0.059s measured on the same machine", line=2),
        span("title144", "Polars 1.44.1", line=3),
        span("m144", "fill_nan 10000 cols 1.557s measured on the same machine", line=4),
        span("note", "Remarks about the benchmark machine and its power settings", line=5),
    )


def build_scenarios() -> dict[str, Scenario]:
    m = measurement_spans()
    ids = tuple(s.span_id for s in m)
    s: dict[str, Scenario] = {}
    s["full_catalog_in_document_order_of_the_context"] = Scenario(
        "fill_nan version", m, context=observed(m, order=tuple(reversed(ids)))
    )
    s["full_catalog_refused_by_span_limit"] = Scenario(
        "Polars 1.44.1 fill_nan",
        m,
        context=observed(m),
        policy=RetrievalBehaviorPolicy(max_spans=3),
    )
    s["full_catalog_refused_by_character_budget"] = Scenario(
        "Polars 1.44.1 fill_nan",
        m,
        context=observed(m),
        policy=RetrievalBehaviorPolicy(character_budget=150),
    )
    s["full_catalog_refused_by_structure_count"] = Scenario(
        "Polars fill_nan",
        m,
        context=observed(m, structure_count=4),
        policy=RetrievalBehaviorPolicy(max_spans=3),
    )
    s["full_catalog_refused_when_not_observed"] = Scenario(
        "Polars 1.44.1 fill_nan",
        m,
        context=observed(m, all_observed=False),
        policy=RetrievalBehaviorPolicy(character_budget=150),
    )
    s["full_catalog_refused_when_context_misses_a_span"] = Scenario(
        "Polars 1.44.1 fill_nan",
        m,
        context=observed(m, order=ids[:-1]),
        policy=RetrievalBehaviorPolicy(max_spans=3),
    )
    s["no_context_uses_the_ranked_path"] = Scenario("Polars 1.44.1 fill_nan", m)
    s["short_title_is_dropped_before_ranking"] = Scenario(
        "Polars 1.44.1 fill_nan", m, policy=RetrievalBehaviorPolicy(max_spans=2)
    )
    s["short_title_survives_when_protected"] = Scenario(
        "Polars 1.44.1 fill_nan",
        m,
        context=observed(m, protected=frozenset({"span:title144"}), all_observed=False),
        policy=RetrievalBehaviorPolicy(max_spans=2),
    )
    s["short_span_with_a_structure_pointer_is_kept"] = Scenario(
        "latency alpha",
        (
            span("kv1", "latency", pointer="/rows/0/metric", line=1),
            span("kv2", "alpha", pointer="/rows/0/condition", line=2),
            span("prose", "Unrelated prose that is long enough to be a normal span", line=3),
        ),
    )
    s["no_hit_keeps_every_non_chrome_span_longest_first"] = Scenario(
        "zzzz qqqq",
        (
            span("p1", "A paragraph of moderate length for the pool", line=1),
            span("p2", "A considerably longer paragraph so that its length ranks it first", line=2),
            span("p3", "Short enough yet kept by the length rule here", line=3),
            span("tiny", "tiny", line=4),
        ),
    )
    chrome = (span("c1", "alpha", line=1), span("c2", "beta", line=2), span("c3", "---", line=3))
    s["all_chrome_falls_back_to_the_whole_pool"] = Scenario("zzzz", chrome)
    s["all_chrome_with_a_hit_keeps_only_the_hit"] = Scenario("alpha", chrome)
    s["span_id_named_in_the_question_ranks_first"] = Scenario(
        "what does span:zeta say about timing",
        (
            span("alpha", "timing information with many words about timing here ok", line=1),
            span("zeta", "an entry that shares no word with the question at all", line=2),
        ),
    )
    s["more_hits_then_longer_text_then_document_order"] = Scenario(
        "latency alpha beta",
        (
            span("w1", "latency alpha beta in a short sentence", line=3),
            span("w2", "latency alpha beta in a somewhat longer sentence than the first", line=2),
            span("w3", "latency alpha only in this one but it is very long indeed so long", line=1),
            span("w4", "latency alpha beta in a short sentence", line=1),
        ),
    )
    s["artifacts_are_interleaved"] = Scenario(
        "alpha beta gamma",
        tuple(
            span(
                f"{a}{i}",
                f"alpha beta gamma entry {a} number {i} padded to pass",
                artifact=f"artifact:{a}",
                line=i,
            )
            for i in range(1, 4)
            for a in ("x", "y", "z")
        ),
        policy=RetrievalBehaviorPolicy(max_spans=5),
    )
    denied = span(
        "denied",
        "alpha denied text that must never be pinned",
        authority=AuthorityState.NOT_ADMISSIBLE,
        line=9,
    )
    s["pinned_first_and_denied_pins_are_ignored"] = Scenario(
        "alpha",
        (
            span("hit1", "alpha is the first long hit sentence in the source", line=1),
            span("hit2", "alpha is the second long hit sentence in the source", line=2),
            span("low", "a pinned line that has no overlap with the question words", line=3),
            denied,
        ),
        pinned=("span:low", "span:denied"),
        policy=RetrievalBehaviorPolicy(max_spans=2),
    )
    s["pinned_beyond_the_span_limit_is_cut"] = Scenario(
        "alpha",
        tuple(
            span(f"p{i}", f"alpha pinned line number {i} in the evidence", line=i)
            for i in range(1, 6)
        ),
        pinned=tuple(f"span:p{i}" for i in range(1, 6)),
        policy=RetrievalBehaviorPolicy(max_spans=3),
    )
    s["oversized_span_is_skipped_and_a_later_one_fits"] = Scenario(
        "alpha",
        (
            span("big", "alpha " + "x" * 80, line=1),
            span("small", "alpha small enough to fit inside the budget", line=2),
        ),
        policy=RetrievalBehaviorPolicy(character_budget=60),
    )
    s["default_cap_of_160_spans"] = Scenario(
        "alpha",
        tuple(
            span(f"n{i:03d}", f"alpha entry number {i:03d} padded to pass the filter", line=i)
            for i in range(1, 201)
        ),
    )
    s["korean_pairs_and_function_words"] = Scenario(
        "온도 관련 경우 압력 때문",
        (
            span("k1", "측정된 온도와 압력을 함께 기록한 표입니다 오늘", line=1),
            span("k2", "관련 경우 때문에 생긴 설명만 있는 문단입니다 여기", line=2),
            span("k3", "온도 기록 문단은 별도로 적어 두었습니다 마지막", line=3),
        ),
    )
    s["a_particle_after_an_identifier_never_matches"] = Scenario(
        "gcc에서 abort하고 원인은",
        (
            span("g1", "GCC miscompiles the _BitInt(33) store and aborts at run time", line=1),
            span("g2", "Clang passes the same test and does not abort at all here", line=2),
        ),
    )
    s["fullwidth_and_case_are_folded"] = Scenario(
        "\uff26\uff29\uff2c\uff2c_\uff2e\uff21\uff2e",
        (
            span("f1", "fill_nan(None) over many columns is slow in the new release", line=1),
            span("f2", "replace over many columns stays fast in the new release", line=2),
        ),
    )
    s["planner_queries_add_search_terms"] = Scenario(
        "why slow",
        (
            span("q1", "benchmark of sorting keys shows the growth per key added", line=1),
            span("q2", "benchmark of hashing shows constant time per element here", line=2),
        ),
        queries=("sorting keys",),
    )
    s["unknown_time_is_used_only_without_eligible_spans"] = Scenario(
        "alpha",
        (
            span(
                "u1",
                "alpha unknown time span that is long enough to count",
                cutoff=CutoffState.UNKNOWN_TIME,
                line=1,
            ),
            span(
                "late",
                "alpha after the cutoff but long enough to be looked at",
                cutoff=CutoffState.AFTER_CUTOFF,
                line=2,
            ),
            denied,
        ),
    )
    s["eligible_spans_hide_unknown_time_spans"] = Scenario(
        "alpha",
        (
            span("e1", "alpha eligible span that is long enough to count here", line=1),
            span(
                "u2",
                "alpha unknown time span that is long enough to count",
                cutoff=CutoffState.UNKNOWN_TIME,
                line=2,
            ),
        ),
    )
    s["only_hit_spans_are_kept_while_room_remains"] = Scenario(
        "latency alpha",
        (
            span("h1", "latency alpha in this long hit sentence for the pool", line=1),
            span("h2", "nothing in common with the question words in this line", line=2),
            span("h3", "another line without any of the question words inside", line=3),
        ),
    )
    s["no_evidence"] = Scenario("alpha", ())
    return s


def ids(*names: str) -> list[str]:
    return [f"span:{n}" for n in names]


# Reviewed by hand against the rules above; independent of the oracle copy.
EXPECTED: dict[str, list[str]] = {
    # FULL catalog: every span, in the order the structure observation gives, short titles too.
    "full_catalog_in_document_order_of_the_context": ids(
        "note", "m144", "title144", "m143", "title143"
    ),
    # Not FULL: the short version titles are chrome, only spans that hit a term are kept.
    "full_catalog_refused_by_span_limit": ids("m143", "m144"),
    "full_catalog_refused_by_character_budget": ids("m143", "m144"),
    "full_catalog_refused_by_structure_count": ids("m143", "m144"),
    "full_catalog_refused_when_not_observed": ids("m143", "m144"),
    "full_catalog_refused_when_context_misses_a_span": ids("m143", "m144"),
    "no_context_uses_the_ranked_path": ids("m143", "m144"),
    "short_title_is_dropped_before_ranking": ids("m143", "m144"),
    "short_title_survives_when_protected": ids("title144", "m143"),
    "short_span_with_a_structure_pointer_is_kept": ids("kv1", "kv2"),
    "no_hit_keeps_every_non_chrome_span_longest_first": ids("p2", "p3", "p1"),
    "all_chrome_falls_back_to_the_whole_pool": ids("c1", "c2", "c3"),
    "all_chrome_with_a_hit_keeps_only_the_hit": ids("c1"),
    "span_id_named_in_the_question_ranks_first": ids("zeta", "alpha"),
    "more_hits_then_longer_text_then_document_order": ids("w2", "w4", "w1", "w3"),
    "artifacts_are_interleaved": ids("x1", "y1", "z1", "x2", "y2"),
    "pinned_first_and_denied_pins_are_ignored": ids("low", "hit2"),
    "pinned_beyond_the_span_limit_is_cut": ids("p1", "p2", "p3"),
    "oversized_span_is_skipped_and_a_later_one_fits": ids("small"),
    "default_cap_of_160_spans": ids(*(f"n{i:03d}" for i in range(1, 161))),
    # 관련·경우·때문 are function words: k2 has no topic term and is dropped.
    "korean_pairs_and_function_words": ids("k1", "k3"),
    # gcc에서 / abort하고 / 원인은 are whole tokens and match nothing: nothing is filtered out.
    "a_particle_after_an_identifier_never_matches": ids("g1", "g2"),
    "fullwidth_and_case_are_folded": ids("f1"),
    "planner_queries_add_search_terms": ids("q1"),
    "unknown_time_is_used_only_without_eligible_spans": ids("u1"),
    "eligible_spans_hide_unknown_time_spans": ids("e1"),
    "only_hit_spans_are_kept_while_room_remains": ids("h1"),
    "no_evidence": [],
}


def test_every_scenario_has_an_expected_selection() -> None:
    assert set(EXPECTED) == set(build_scenarios())


@pytest.mark.parametrize("name", list(build_scenarios()))
def test_current_selection_equals_the_oracle(name: str) -> None:
    sc = build_scenarios()[name]
    args = (sc.question, sc.queries, sc.evidence, sc.pinned, sc.context, sc.policy)
    assert current_select(*args) == oracle_select(*args)


@pytest.mark.parametrize("name", list(build_scenarios()))
def test_current_selection_matches_the_reviewed_expectation(name: str) -> None:
    sc = build_scenarios()[name]
    args = (sc.question, sc.queries, sc.evidence, sc.pinned, sc.context, sc.policy)
    assert current_select(*args) == EXPECTED[name]


SEARCH_TERMS: dict[str, set[str]] = {
    "Polars 1.44에서 fill_nan(None)이 열 수가 많을수록 느려진 원인은 무엇인가?": {
        "polars",
        "1.44에서",
        "fill_nan",
        "none",
        "수가",
        "많을수록",
        "느려진",
        "원인은",
        "무엇인가",
    },
    "GCC에서 _BitInt(33) abort": {"gcc에서", "_bitint", "abort"},
    "경우 관련 온도 압력 and 열": {"온도", "압력", "and"},
    "v1.2와 v1.20 collect_all": {"v1.2와", "v1.20", "collect_all"},
    "C:\\data\\보고서.pdf를 확인": {"data", "보고서.pdf를", "확인"},
    "\uff26\uff29\uff2c\uff2c_\uff2e\uff21\uff2e": {"fill_nan"},
    "a b": set(),
}


@pytest.mark.parametrize("text", list(SEARCH_TERMS))
def test_search_terms_are_pinned(text: str) -> None:
    assert research_retrieval.search_terms(text) == o_search_terms(text) == SEARCH_TERMS[text]


# ---------------------------------------------------------------------------------------------
# Seeded random corpora: the same ordered IDs from the oracle and the current function.
# ---------------------------------------------------------------------------------------------
WORDS = (
    "latency",
    "alpha",
    "beta",
    "gamma",
    "sort",
    "keys",
    "duckdb",
    "polars",
    "fill_nan",
    "collect_all",
    "LazyFrame",
    "GCC",
    "_BitInt",
    "v1.44.1",
    "1.43.2",
    "ADC",
    "온도",
    "압력",
    "시장",
    "측정",
    "원인",
    "관련",
    "경우",
    "때문",
    "정렬",
    "지연",
    "열",
    "수",
    "gcc에서",
    "abort하고",
    "benchmark",
    "table",
    "header",
    "unit",
    "seconds",
    "cols",
    "rows",
    "ratio",
    "slope",
)
SHORT = ("Polars 1.44.1", "Results", "---", "article", "Table 2", "v1.20", "ms", "열 수")
STATES = (
    (AuthorityState.INFORMAL, CutoffState.ELIGIBLE),
    (AuthorityState.OFFICIAL, CutoffState.ELIGIBLE),
    (AuthorityState.INFORMAL, CutoffState.UNKNOWN_TIME),
    (AuthorityState.INFORMAL, CutoffState.AFTER_CUTOFF),
    (AuthorityState.NOT_ADMISSIBLE, CutoffState.ELIGIBLE),
)


def random_scenario(rng: random.Random, number: int) -> Scenario:
    artifacts = [f"artifact:{c}" for c in "abc"[: rng.randint(1, 3)]]
    spans: list[EvidenceSpan] = []
    for i in range(rng.randint(0, 60)):
        text = (
            rng.choice(SHORT)
            if rng.random() < 0.3
            else " ".join(rng.choice(WORDS) for _ in range(rng.randint(2, 18)))
        )
        authority, cutoff = STATES[0] if rng.random() < 0.8 else rng.choice(STATES)
        pointer = f"/rows/{i // 3}/{rng.choice(('a', 'b', 'c'))}" if rng.random() < 0.15 else None
        spans.append(
            span(
                f"r{number}-{i}",
                text,
                artifact=rng.choice(artifacts),
                line=rng.choice((None, rng.randint(1, 50))),
                page=rng.choice((None, rng.randint(1, 4))),
                pointer=pointer,
                authority=authority,
                cutoff=cutoff,
            )
        )
    evidence = tuple(spans)
    eligible = [s.span_id for s in connected_retrieval_spans(evidence)]
    question = " ".join(rng.choice(WORDS) for _ in range(rng.randint(1, 6)))
    if eligible and rng.random() < 0.1:
        question += " " + rng.choice(eligible)
    queries = tuple(" ".join(rng.choice(WORDS) for _ in range(2)) for _ in range(rng.randint(0, 2)))
    pinned = tuple(rng.sample([s.span_id for s in spans], k=min(len(spans), rng.randint(0, 3))))
    kind = rng.choice(("none", "full", "unobserved", "missing", "protected"))
    context: CandidateContext | None = None
    if kind == "full":
        refs = [s.span_id for s in connected_retrieval_spans(evidence)]
        rng.shuffle(refs)
        context = observed(evidence, order=tuple(refs), structure_count=rng.randint(0, 5))
    elif kind == "unobserved":
        context = observed(evidence, all_observed=False)
    elif kind == "missing":
        context = observed(evidence, order=tuple(eligible[:-1]))
    elif kind == "protected":
        context = observed(
            evidence,
            protected=frozenset(rng.sample(eligible, k=min(len(eligible), 2))),
            all_observed=rng.random() < 0.5,
            order=tuple(eligible),
        )
    policy = RetrievalBehaviorPolicy(
        max_spans=rng.choice((3, 8, 20, 160)), character_budget=rng.choice((200, 600, 50000))
    )
    return Scenario(question, evidence, queries, pinned, context, policy)


def random_scenarios() -> list[Scenario]:
    rng = random.Random(20261027)
    return [random_scenario(rng, n) for n in range(120)]


def test_random_corpora_select_the_same_ordered_spans() -> None:
    for sc in random_scenarios():
        args = (sc.question, sc.queries, sc.evidence, sc.pinned, sc.context, sc.policy)
        assert current_select(*args) == oracle_select(*args)


def all_results() -> list[list[str]]:
    results: list[list[str]] = []
    everything = [*build_scenarios().values(), *random_scenarios()]
    for sc in everything:
        results.append(
            current_select(sc.question, sc.queries, sc.evidence, sc.pinned, sc.context, sc.policy)
        )
    return results


PINNED_DIGEST = "b41d59271a44cc049dc83d103afede5f19b9e50f888b577a60daaa00d72a462f"


def test_every_selection_is_pinned_by_one_digest() -> None:
    digest = domain_digest(
        "S1_RETRIEVAL_SELECTION_PIN", "1.0.0", canonical_payload({"results": all_results()})
    )
    assert digest == PINNED_DIGEST


def test_the_random_corpora_exercise_each_branch() -> None:
    kinds: dict[str, int] = defaultdict(int)
    for sc in random_scenarios():
        eligible = connected_retrieval_spans(sc.evidence)
        full = o_full_catalog(eligible, sc.context, sc.policy) is not None
        kinds["full" if full else "ranked"] += 1
        kinds["pinned" if sc.pinned else "unpinned"] += 1
        kinds["queries" if sc.queries else "no_queries"] += 1
        if any(o_is_chrome(s) for s in eligible):
            kinds["has_chrome"] += 1
    assert min(kinds.values()) >= 5
    assert set(kinds) >= {
        "full",
        "ranked",
        "pinned",
        "unpinned",
        "queries",
        "no_queries",
        "has_chrome",
    }


# ---------------------------------------------------------------------------------------------
# Evaluation. Until S3 the self-improvement evaluation selected with an older scoring function
# and picked other spans than the research path for the same input; S1 pinned seven of those
# differences and asserted that they differed. S3 sends the evaluation through the function the
# research path calls, so those seven tests now assert the opposite on purpose (a specification
# change, not a loosened expectation): the evaluation selects what the research path selects.
# The older scoring function still serves the older analysis path and is pinned unchanged below.
# ---------------------------------------------------------------------------------------------
def evaluation_payload(sc: Scenario) -> dict[str, object]:
    payload: dict[str, object] = {
        "problem": sc.question,
        "evidence": [s.model_dump(mode="json") for s in sc.evidence],
        "queries": list(sc.queries),
        "pinned_refs": list(sc.pinned),
    }
    if sc.context is not None:
        catalog: dict[str, object] = {
            "ordered_refs": list(sc.context.ordered_refs),
            "protected_refs": sorted(sc.context.protected_refs),
            "all_observed": sc.context.all_observed,
            "structure_count": sc.context.structure_count,
        }
        payload["catalog"] = catalog
    return payload


def evaluation_select(sc: Scenario) -> list[str]:
    result = RetrievalBehaviorHandler().evaluate(
        sc.policy, cast(dict[str, JsonValue], evaluation_payload(sc))
    )
    return cast(list[str], result["selected_refs"])


FORMER_EVALUATION_SCORER: dict[str, list[str]] = {
    "no_context_uses_the_ranked_path": ids("m143", "m144", "title143", "title144", "note"),
    "short_title_survives_when_protected": ids("m143", "m144"),
    "artifacts_are_interleaved": ids("x1", "x2", "x3", "y1", "y2"),
    "korean_pairs_and_function_words": ids("k1", "k2", "k3"),
    "fullwidth_and_case_are_folded": ids("f1", "f2"),
    "all_chrome_with_a_hit_keeps_only_the_hit": ids("c1", "c2", "c3"),
    "only_hit_spans_are_kept_while_room_remains": ids("h1", "h2", "h3"),
}


@pytest.mark.parametrize("name", list(FORMER_EVALUATION_SCORER))
def test_the_evaluation_now_selects_what_the_research_path_selects(name: str) -> None:
    sc = build_scenarios()[name]
    assert evaluation_select(sc) == EXPECTED[name]
    assert evaluation_select(sc) != FORMER_EVALUATION_SCORER[name]


@pytest.mark.parametrize("name", list(FORMER_EVALUATION_SCORER))
def test_the_older_scoring_function_is_unchanged_for_the_older_analysis_path(name: str) -> None:
    sc = build_scenarios()[name]
    older = select_evidence_context(
        problem=sc.question,
        evidence=sc.evidence,
        max_spans=sc.policy.max_spans,
        character_budget=sc.policy.character_budget,
    )
    assert [s.span_id for s in older.selected] == FORMER_EVALUATION_SCORER[name]
