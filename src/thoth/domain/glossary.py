"""A Korean-to-English term list and the matcher that expands a question from it.

The expansion is a lane of its own (GLOSSARY_BM25), never a rewrite of the question: the matcher
finds Korean terms in the natural-language part of the question and gives back the English terms
they stand for, and the lane ranks structure groups by those English terms alone. What it does:

- Typed identifiers (paths, versions, flags, symbols, quantities ...) are found first by the same
  scanner the PROTECTED_EXACT lane uses and are masked, so nothing inside one is ever expanded.
- A term is found as the longest phrase at its place, left to right, without overlaps. Spaces inside
  a term match zero or more spaces ("정렬키" and "정렬 키"). On the left the text must not continue
  the word; on the right it may end, or continue only with a closed list of Korean particles.
- A Korean term that stands for more than one English term in the list is ambiguous and is not
  expanded. A term of one syllable is never matched alone.
- Only the Korean to English direction exists. An English term is never expanded back, and an
  expansion is never expanded again. At most MAX_EXPANSIONS English terms are used per question.

The list is pinned by a digest over its entries; a policy names the digest it was made for.
Everything here is pure.
"""

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from thoth.domain.query_identifiers import fold, scan_query

GLOSSARY_FORMAT = "glossary-format:1.0.0"
GLOSSARY_MATCHER_VERSION = "glossary-matcher:1.0.0"
MAX_EXPANSIONS = 10
MAX_ENTRIES = 5000

type GlossaryConfidence = Literal["HIGH", "MEDIUM"]

# Closed list of Korean particles that may follow a term. A change is a new matcher version.
PARTICLES = (
    "에서는",
    "으로는",
    "에서",
    "으로",
    "에는",
    "로는",
    "이랑",
    "이나",
    "이란",
    "입니다",
    "까지",
    "부터",
    "처럼",
    "보다",
    "이다",
    "이",
    "가",
    "은",
    "는",
    "을",
    "를",
    "의",
    "에",
    "로",
    "와",
    "과",
    "도",
    "만",
    "나",
    "랑",
    "들",
    "란",
    "인",
)
_WORD_CHAR = "가-힣A-Za-z0-9_"
_MASK = "\x00"


@dataclass(frozen=True)
class GlossaryEntry:
    entry_id: str
    ko: str
    en: str
    domain: str
    confidence: GlossaryConfidence


def entry_id(ko: str, en: str, domain: str) -> str:
    """The identity of an entry is its content: the same three fields always give the same id."""
    return hashlib.sha256(f"{ko}\x1f{en}\x1f{domain}".encode()).hexdigest()[:16]


def make_entry(ko: str, en: str, domain: str, confidence: GlossaryConfidence) -> GlossaryEntry:
    return GlossaryEntry(entry_id(ko, en, domain), ko, en, domain, confidence)


def entry_matches_its_id(entry: GlossaryEntry) -> bool:
    return entry.entry_id == entry_id(entry.ko, entry.en, entry.domain)


def glossary_digest(entries: Iterable[GlossaryEntry]) -> str:
    """Digest over (id, ko, en, domain, confidence) of every entry, in id order, and the format."""
    ordered = sorted(entries, key=lambda e: e.entry_id)
    body = {
        "format": GLOSSARY_FORMAT,
        "entries": [[e.entry_id, e.ko, e.en, e.domain, e.confidence] for e in ordered],
    }
    text = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass(frozen=True, eq=False)
class Glossary:
    """Entries and the digest they are known by.

    `verified` says the digest was computed from these entries. A case of a behavior evaluation
    carries only the entries that can matter for it, with the digest of the whole list it was made
    from; that digest is claimed, not recomputed, so such a glossary is not verified.
    """

    entries: tuple[GlossaryEntry, ...]
    digest: str
    verified: bool = True


def build_glossary(entries: Sequence[GlossaryEntry]) -> Glossary:
    return Glossary(tuple(entries), glossary_digest(entries))


@dataclass(frozen=True)
class FiredTerm:
    matched: str
    en: str
    entry_ids: tuple[str, ...]


@dataclass(frozen=True)
class AbstainedTerm:
    matched: str
    reason: Literal["AMBIGUOUS"]


@dataclass(frozen=True)
class GlossaryMatches:
    fired: tuple[FiredTerm, ...]
    abstained: tuple[AbstainedTerm, ...]
    expansions: tuple[str, ...]
    # English terms that were found but left out because MAX_EXPANSIONS was reached.
    dropped: int = 0


@dataclass(frozen=True)
class GlossaryDetail:
    """What a selection used the glossary for, for the evaluation output and the journal."""

    digest: str
    min_confidence: GlossaryConfidence
    entries_carried: int
    verified: bool
    matches: GlossaryMatches


_PARTICLE_ALTERNATION = "|".join(sorted(PARTICLES, key=lambda p: (-len(p), p)))


@lru_cache(maxsize=8192)
def _pattern(ko: str) -> re.Pattern[str]:
    body = r"\s*".join(re.escape(part) for part in ko.split())
    return re.compile(
        rf"(?<![{_WORD_CHAR}]){body}(?=(?:{_PARTICLE_ALTERNATION}){{0,2}}(?![{_WORD_CHAR}]))"
    )


def _squeezed(text: str) -> str:
    return "".join(text.split())


def _matchable(ko: str) -> bool:
    return len(_squeezed(ko)) >= 2


def _masked(text: str) -> str:
    fields = scan_query(text)
    chars = list(fields.normalized)
    for identifier in fields.identifiers:
        chars[identifier.start : identifier.end] = _MASK * (identifier.end - identifier.start)
    return fold("".join(chars))


def match_glossary(
    texts: Sequence[str], glossary: Glossary, min_confidence: GlossaryConfidence
) -> GlossaryMatches:
    """The English terms the Korean terms of `texts` stand for, and what was held back and why."""
    english: dict[str, set[str]] = {}
    for entry in glossary.entries:
        english.setdefault(fold(entry.ko), set()).add(entry.en)
    allowed = {"HIGH"} if min_confidence == "HIGH" else {"HIGH", "MEDIUM"}
    used: dict[str, list[GlossaryEntry]] = {}
    for entry in glossary.entries:
        if entry.confidence in allowed and _matchable(entry.ko):
            used.setdefault(fold(entry.ko), []).append(entry)
    fired: list[FiredTerm] = []
    abstained: list[AbstainedTerm] = []
    expansions: list[str] = []
    dropped = 0
    for text in texts:
        masked = _masked(text)
        found: list[tuple[int, int, str]] = []
        for ko in used:
            found.extend((m.start(), m.end(), ko) for m in _pattern(ko).finditer(masked))
        found.sort(key=lambda item: (item[0], -(item[1] - item[0]), item[2]))
        cursor = 0
        for start, end, ko in found:
            if start < cursor:
                continue
            cursor = end
            phrase = masked[start:end]
            if len(english[ko]) > 1:
                abstained.append(AbstainedTerm(phrase, "AMBIGUOUS"))
                continue
            (term,) = english[ko]
            if term in expansions:
                continue
            if len(expansions) >= MAX_EXPANSIONS:
                dropped += 1
                continue
            expansions.append(term)
            ids = tuple(sorted(e.entry_id for e in used[ko] if e.en == term))
            fired.append(FiredTerm(phrase, term, ids))
    return GlossaryMatches(tuple(fired), tuple(abstained), tuple(expansions), dropped)


def entries_that_can_matter(texts: Sequence[str], glossary: Glossary) -> tuple[GlossaryEntry, ...]:
    """The entries whose Korean term occurs in a text once spaces are left out.

    This is a superset of what `match_glossary` can fire on those texts whatever the confidence
    floor: a match needs the term's parts in a row, optionally separated by spaces. It keeps every
    entry of a term that occurs, so ambiguity is judged on the same entries as with the whole list.
    """
    squeezed = [_squeezed(fold(unicodedata.normalize("NFKC", text))) for text in texts]
    return tuple(
        entry
        for entry in glossary.entries
        if _matchable(entry.ko) and any(_squeezed(fold(entry.ko)) in text for text in squeezed)
    )
