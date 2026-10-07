"""The words of a text, for deciding whether a question and a memory are about the same thing.

Korean is written with endings and particles stuck to the word ("자료를", "필요해"), so cutting a
text at spaces cannot tell that "자료를" and "자료" are one word. A run of Hangul, Han or kana
letters therefore keeps two things: the run as written, and, when it has three or more letters,
every overlapping pair of letters in it (a "bigram": 자료를 -> 자료, 료를). A two-letter run is
only itself. Letters of other scripts and digits stay whole, from two characters up ("AI", "5G").

A question word counts as matched when the run or any pair of it is among the memory's terms.
Counting is by question word: one word is matched once, however many of its pairs are found, so
the pairs never make a match look bigger. No score or weight is made.

The idea of overlapping letter pairs for Korean text comes from public work on keyword search
over agent memory; this code is written here from that idea alone.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import cached_property, lru_cache

_LETTERS = "\u1100-\u11ff\u3040-\u30ff\u3130-\u318f\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7a3"
_RUN = re.compile(rf"[{_LETTERS}]+|(?:(?![{_LETTERS}])\w)+")
_PAIR_LETTERS = re.compile(rf"[{_LETTERS}]+")
# A run longer than this makes pairs only from its start; nothing is rejected.
MAX_PAIR_CHARS = 2_000
# Pairs that are only a grammatical ending or helper: they would match unrelated sentences.
_ENDING_PAIRS = frozenset(
    {
        "하다",
        "한다",
        "했다",
        "되다",
        "된다",
        "있다",
        "없다",
        "이다",
        "니다",
        "습니",
        "합니",
        "에서",
        "에게",
        "으로",
        "까지",
        "부터",
        "해서",
        "하고",
        "하는",
    }
)
# Two-letter English words that say nothing about a topic ("AI" and "5G" do).
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "as",
        "at",
        "be",
        "by",
        "do",
        "go",
        "he",
        "if",
        "in",
        "is",
        "it",
        "me",
        "my",
        "no",
        "of",
        "on",
        "or",
        "so",
        "to",
        "up",
        "us",
        "we",
    }
)


@dataclass(frozen=True)
class Word:
    run: str
    pairs: tuple[str, ...] = ()

    @cached_property
    def forms(self) -> frozenset[str]:
        return frozenset((self.run, *self.pairs))


def _normal(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _pairs(run: str) -> tuple[str, ...]:
    head = run[:MAX_PAIR_CHARS]
    found = (head[at : at + 2] for at in range(len(head) - 1))
    return tuple(dict.fromkeys(pair for pair in found if pair not in _ENDING_PAIRS))


@lru_cache(maxsize=2_048)
def words(text: str) -> tuple[Word, ...]:
    """The words of a text in order of first appearance, each once."""
    found: dict[str, Word] = {}
    for match in _RUN.finditer(_normal(text)):
        run = match.group(0)
        if len(run) < 2 or run in found:
            continue
        if _PAIR_LETTERS.fullmatch(run):
            found[run] = Word(run, _pairs(run) if len(run) >= 3 else ())
        elif run not in _STOP_WORDS:
            found[run] = Word(run)
    return tuple(found.values())


@lru_cache(maxsize=2_048)
def terms(text: str) -> frozenset[str]:
    """Every run and every pair of the text."""
    return frozenset(form for word in words(text) for form in word.forms)


def matched_words(query: Sequence[Word], memory_terms: frozenset[str]) -> tuple[Word, ...]:
    """The question words that appear in the memory, each counted once."""
    return tuple(word for word in query if not word.forms.isdisjoint(memory_terms))


def word_runs(items: Iterable[Word]) -> frozenset[str]:
    return frozenset(item.run for item in items)


def _same_word(left: Word, right: Word) -> bool:
    """One word is the other, or a letter pair of it: 자료 and 자료를 are one word."""
    return left.run in right.forms or right.run in left.forms


def added_words(text: str, existing: Sequence[Word]) -> tuple[Word, ...]:
    """The words of an added text that the question does not already have.

    A word that is, or is a letter pair of, a word already kept (the question's own or an earlier
    added one) is dropped, so it can never be counted a second time.
    """
    kept: list[Word] = []
    for word in words(text):
        if not any(_same_word(word, other) for other in (*existing, *kept)):
            kept.append(word)
    return tuple(kept)
