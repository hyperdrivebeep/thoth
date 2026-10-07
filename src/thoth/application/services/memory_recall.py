"""How many memories an investigation gets, and which, once they are allowed at all.

The limits are starting values to be tuned against fixed questions, not standards. The order
uses no score or probability: an id or source named in the question, then an exact phrase, then
how many question words match, then a spread across kinds, then newest, then id.

An automatic memory (a hypothesis, action or outcome saved after an investigation) is let in more
narrowly than a user's correction: the question must name it, quote it, be a follow-up question
("앞에서 세운 가설"), or share two words that are not words every memory of the project shares.
Counting words is all this does; no score is made. Words are counted per question word: a question
word is matched once when it, or a pair of its letters, is in the memory (see memory_terms.py).
The memory's words are made from its text when it is recalled, not read from the words stored with
it, so memories saved under an older splitting rule are found by the current one.

An automatic memory also needs at least one question word found as written (not only through a
letter pair of it) besides the two; a word met only through a pair is marked as a partial match.

Words a model added to the question (memory_expansion.py) take part the same way, in a second
group: a memory the question's own words reach comes first, one reached only with the added words
follows, and the count an automatic memory needs is made from both. An added word that the
question already has (itself, or one letter pair of it) is not counted again.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

from thoth.domain.enums import MemoryKind, MemoryPayloadMode
from thoth.domain.memory import FullMemoryRevision, MemoryMatch
from thoth.domain.memory_terms import Word, matched_words, terms, word_runs

OMITTED_BY_BUDGET = "OMITTED_BY_BUDGET"
AUTO_MEMORY_WEAK_MATCH = "AUTO_MEMORY_WEAK_MATCH"
AUTO_MEMORY_NO_EVIDENCE = "AUTO_MEMORY_NO_EVIDENCE"
QUERY_IRRELEVANT = "QUERY_IRRELEVANT"
# Words that mark a question as continuing the previous investigation. One place, one list.
FOLLOW_UP_MARKERS = (
    "앞에서",
    "아까",
    "방금",
    "이전",
    "지난번",
    "위에서",
    "그 가설",
    "그 행동",
    "그 결과",
    "앞의",
    "previous",
    "earlier",
    "above",
)
# Fewer memories than this cannot say which words are shared by "most of the project".
COMMON_TERM_MIN_POOL = 4
# A hypothesis or action is backed by source spans; a measured outcome by its execution record.
_EVIDENCE_KINDS = frozenset({MemoryKind.HYPOTHESIS, MemoryKind.ACTION})
_CHARS_PER_TOKEN = 2
_MIN_KEY = 6


@dataclass(frozen=True)
class RecallLimits:
    candidates: int = 24
    final: int = 8
    per_kind: int = 3
    per_key: int = 2
    item_tokens: int = 320
    total_tokens: int = 1_600

    def as_dict(self) -> dict[str, int]:
        return {
            "candidates": self.candidates,
            "final": self.final,
            "per_kind": self.per_kind,
            "per_key": self.per_key,
            "item_tokens": self.item_tokens,
            "total_tokens": self.total_tokens,
        }


def estimate_tokens(text: str) -> int:
    """A character-count estimate (about two characters a token); recorded usage corrects it."""

    return (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


def _key(item: FullMemoryRevision) -> str:
    return item.source_ref or f"OWNER:{item.owner_revision_ref}"


def _phrase(item: FullMemoryRevision) -> str:
    """The memory's own sentence: a correction's text, or the line after a new memory's header."""

    if item.assertion is not None:
        return item.assertion
    lines = item.content_excerpt.split("\n", 1)
    return lines[1] if len(lines) == 2 and not lines[1].lstrip().startswith("{") else ""


def is_auto_memory(item: FullMemoryRevision) -> bool:
    return item.payload_mode == MemoryPayloadMode.DOMAIN_REFERENCE


def auto_memory_lacks_evidence(
    payload_mode: MemoryPayloadMode, kind: MemoryKind, evidence_refs: Sequence[str]
) -> bool:
    """An automatic hypothesis or action with no source span behind it is not remembered."""

    return (
        payload_mode == MemoryPayloadMode.DOMAIN_REFERENCE
        and kind in _EVIDENCE_KINDS
        and not evidence_refs
    )


def lacks_evidence(item: FullMemoryRevision) -> bool:
    return auto_memory_lacks_evidence(item.payload_mode, item.kind, item.evidence_refs)


def follow_up_markers(query: str) -> tuple[str, ...]:
    text = _norm(query)
    found: list[str] = []
    for marker in FOLLOW_UP_MARKERS:
        # An English marker must be a whole word; a Korean one is found inside the sentence.
        pattern = (
            rf"(?<![a-z0-9]){re.escape(marker)}(?![a-z0-9])"
            if marker.isascii()
            else re.escape(marker)
        )
        if re.search(pattern, text):
            found.append(marker)
    return tuple(found)


_LABEL = re.compile(r"^[A-Za-z_][\w ]{0,40}:\s+")


def recall_text(item: FullMemoryRevision) -> str:
    """The words a memory is found by: a correction's text, or a new memory's values without the
    reference line and the field names that every memory of a kind shares."""

    if item.assertion is not None:
        return item.assertion
    excerpt = item.content_excerpt
    if item.source_ref and excerpt.startswith(item.source_ref + "\n"):
        excerpt = excerpt[len(item.source_ref) + 1 :]
    first, *rest = excerpt.split(" | ")
    return " ".join([first, *(_LABEL.sub("", part) for part in rest)])


def item_terms(item: FullMemoryRevision) -> frozenset[str]:
    return terms(recall_text(item))


def common_terms(
    eligible: Sequence[FullMemoryRevision], query_words: Sequence[Word]
) -> frozenset[str]:
    """Question words that appear in at least half of the memories allowed at all; counting only."""

    if len(eligible) < COMMON_TERM_MIN_POOL:
        return frozenset()
    counts: dict[str, int] = defaultdict(int)
    for item in eligible:
        for run in word_runs(matched_words(query_words, item_terms(item))):
            counts[run] += 1
    return frozenset(run for run, count in counts.items() if count * 2 >= len(eligible))


@dataclass
class Narrowing:
    relevant: list[FullMemoryRevision] = field(default_factory=lambda: [])
    excluded: dict[str, list[str]] = field(default_factory=lambda: {})
    common_terms: frozenset[str] = frozenset()
    follow_up_markers: tuple[str, ...] = ()
    # Memories that only the added words let in, and how each memory met the question.
    expansion_only: frozenset[str] = frozenset()
    matches: dict[str, MemoryMatch] = field(default_factory=lambda: {})


def narrow_recall(
    eligible: Sequence[FullMemoryRevision],
    query: str,
    query_words: Sequence[Word],
    added_words: Sequence[Word] = (),
) -> Narrowing:
    """Which of the memories allowed at all meet this question, and why the others do not."""

    markers = follow_up_markers(query)
    own_common = common_terms(eligible, query_words)
    common = common_terms(eligible, (*query_words, *added_words)) if added_words else own_common
    narrowing = Narrowing(common_terms=common, follow_up_markers=markers)
    text = _norm(query)
    later: set[str] = set()

    def leave_out(item: FullMemoryRevision, reason: str) -> None:
        narrowing.excluded.setdefault(reason, []).append(item.memory_revision_id)

    def let_in(
        item: FullMemoryRevision,
        own: frozenset[str],
        own_whole: frozenset[str],
        added: frozenset[str],
        added_whole: frozenset[str],
    ) -> None:
        narrowing.relevant.append(item)
        by_expansion = item.memory_revision_id in later
        added = added if by_expansion else frozenset()
        partial, partial_added = own - own_whole, added - added_whole
        narrowing.matches[item.memory_revision_id] = MemoryMatch(
            matched_by="EXPANSION" if by_expansion else "QUERY",
            words=tuple(sorted(own)),
            added_words=tuple(sorted(added)),
            match_strength="PARTIAL" if partial or partial_added else "WHOLE",
            partial_words=tuple(sorted(partial)),
            partial_added_words=tuple(sorted(partial_added)),
        )

    for item in eligible:
        item_words = item_terms(item)
        met = matched_words(query_words, item_words)
        shared = word_runs(met)
        whole = frozenset(word.run for word in met if word.run in item_words)
        met_added = matched_words(added_words, item_words)
        added = word_runs(met_added)
        added_whole = frozenset(word.run for word in met_added if word.run in item_words)
        if markers or not query_words:
            let_in(item, shared, whole, frozenset(), frozenset())
        elif not is_auto_memory(item):
            # A user's correction needs one matched word, as before.
            if shared:
                let_in(item, shared, whole, frozenset(), frozenset())
            elif added:
                later.add(item.memory_revision_id)
                let_in(item, shared, whole, added, added_whole)
            else:
                leave_out(item, QUERY_IRRELEVANT)
        elif (
            _names_it(item, text)
            or _quotes_it(item, text)
            or (len(shared - own_common) >= 2 and whole - own_common)
        ):
            let_in(item, shared, whole, frozenset(), frozenset())
        elif len((shared | added) - common) >= 2 and added_whole - common:
            later.add(item.memory_revision_id)
            let_in(item, shared, whole, added, added_whole)
        elif not shared and not added:
            leave_out(item, QUERY_IRRELEVANT)
        else:
            leave_out(item, AUTO_MEMORY_WEAK_MATCH)
    narrowing.expansion_only = frozenset(later)
    return narrowing


def _quotes_it(item: FullMemoryRevision, text: str) -> bool:
    phrase = _norm(_phrase(item))
    return len(phrase) >= _MIN_KEY and phrase in text


def _names_it(item: FullMemoryRevision, query: str) -> bool:
    keys = {item.memory_id, item.memory_revision_id, item.source_ref or ""}
    if item.source_ref and ":" in item.source_ref:
        keys.add(item.source_ref.split(":", 1)[1])
    return any(len(key) >= _MIN_KEY and _norm(key) in query for key in keys)


def order_recall(
    items: Sequence[FullMemoryRevision],
    query: str,
    query_words: Sequence[Word],
    follow_up: bool = False,
) -> list[FullMemoryRevision]:
    text = _norm(query)
    groups: dict[tuple[bool, bool, int], list[FullMemoryRevision]] = defaultdict(list)
    for item in items:
        # A follow-up has no words to count: a user's correction goes first, the rest by recency.
        third = (
            int(not is_auto_memory(item))
            if follow_up
            else len(matched_words(query_words, item_terms(item)))
        )
        groups[(_names_it(item, text), _quotes_it(item, text), third)].append(item)
    ordered: list[FullMemoryRevision] = []
    for rank in sorted(groups, reverse=True):
        by_kind: dict[str, list[FullMemoryRevision]] = defaultdict(list)
        for item in sorted(groups[rank], key=lambda i: (-i.created_at.timestamp(), i.memory_id)):
            by_kind[item.kind.value].append(item)
        queues = [by_kind[kind] for kind in sorted(by_kind)]
        while any(queues):
            for queue in queues:
                if queue:
                    ordered.append(queue.pop(0))
    return ordered


@dataclass
class RecallPlan:
    retrieved: list[FullMemoryRevision] = field(default_factory=lambda: [])
    selected: list[FullMemoryRevision] = field(default_factory=lambda: [])
    included: list[FullMemoryRevision] = field(default_factory=lambda: [])
    omitted: list[FullMemoryRevision] = field(default_factory=lambda: [])
    omitted_by_limit: dict[str, int] = field(default_factory=lambda: {})
    truncated: list[str] = field(default_factory=lambda: [])
    tokens: int = 0


def plan_recall(
    candidates: Sequence[FullMemoryRevision],
    query: str,
    query_words: Sequence[Word],
    limits: RecallLimits,
    follow_up: bool = False,
    added_words: Sequence[Word] = (),
    expansion_only: frozenset[str] = frozenset(),
) -> RecallPlan:
    """Narrow the memories that may be recalled to the ones the question is given."""

    plan = RecallPlan()
    ordered = order_recall(
        [i for i in candidates if i.memory_revision_id not in expansion_only],
        query,
        query_words,
        follow_up,
    ) + order_recall(
        [i for i in candidates if i.memory_revision_id in expansion_only],
        query,
        (*query_words, *added_words),
        follow_up,
    )
    plan.retrieved = ordered[: limits.candidates]

    def leave_out(item: FullMemoryRevision, limit: str) -> None:
        plan.omitted.append(item)
        plan.omitted_by_limit[limit] = plan.omitted_by_limit.get(limit, 0) + 1

    for item in ordered[limits.candidates :]:
        leave_out(item, "candidates")
    kinds: dict[str, int] = defaultdict(int)
    keys: dict[str, int] = defaultdict(int)
    for item in plan.retrieved:
        if len(plan.selected) >= limits.final:
            leave_out(item, "final")
        elif kinds[item.kind.value] >= limits.per_kind:
            leave_out(item, "per_kind")
        elif keys[_key(item)] >= limits.per_key:
            leave_out(item, "per_key")
        else:
            kinds[item.kind.value] += 1
            keys[_key(item)] += 1
            plan.selected.append(item)
    for item in plan.selected:
        room = limits.item_tokens * _CHARS_PER_TOKEN
        shown = (
            item
            if len(item.content_excerpt) <= room
            else item.model_copy(update={"content_excerpt": item.content_excerpt[:room]})
        )
        tokens = estimate_tokens(shown.content_excerpt)
        if plan.tokens + tokens > limits.total_tokens:
            leave_out(item, "total_tokens")
            continue
        if shown is not item:
            plan.truncated.append(item.memory_revision_id)
        plan.included.append(shown)
        plan.tokens += tokens
    return plan
