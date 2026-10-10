"""Which script a readable text is written in, for a question written in Hangul.

A Hangul question gets Hangul text. Chinese characters and kana are a problem unless the question
or the supplied evidence has them (a quotation). A question without Hangul takes any text. The
katakana middle dot is punctuation, not a kana letter: it is never a problem, and the one thing
changed here is that a model that wrote it as a separator gets the Korean middle dot instead.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

HANGUL = re.compile("[가-힣]")
HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
# Kana letters only: U+30FB (the katakana middle dot) is punctuation and is left out.
KANA = re.compile(r"[\u3040-\u30fa\u30fc-\u30ff]")
_FOREIGN_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30fa\u30fc-\u30ff]+")
KATAKANA_MIDDLE_DOT = "\u30fb"
KOREAN_MIDDLE_DOT = "\u00b7"


def korean_middle_dot(text: str) -> str:
    """The text with every katakana middle dot written as the Korean middle dot."""
    return text.replace(KATAKANA_MIDDLE_DOT, KOREAN_MIDDLE_DOT)


def language_problem(text: str, problem: str) -> str | None:
    """LANGUAGE when a Hangul question gets text with no Hangul, SCRIPT when the text has Chinese
    characters or kana the question has none of, None otherwise (a question without Hangul takes
    any text)."""
    if not HANGUL.search(problem):
        return None
    if not HANGUL.search(text):
        return "LANGUAGE"
    foreign = HAN.search(text) or KANA.search(text)
    return "SCRIPT" if foreign and not (HAN.search(problem) or KANA.search(problem)) else None


class Quotes:
    """What the question and the supplied evidence say, read only when a text needs checking."""

    def __init__(self, problem: str, evidence: Callable[[], Iterable[str]]) -> None:
        self._problem = problem
        self._evidence = evidence
        self._corpus: str | None = None

    def has(self, fragment: str) -> bool:
        if self._corpus is None:
            self._corpus = "\n".join((self._problem, *self._evidence()))
        return fragment in self._corpus


def visible_problem(text: str, problem: str, quotes: Quotes) -> str | None:
    """language_problem, except that characters quoted from the question or the evidence are fine:
    every run of Chinese characters or kana must be in what was quoted, and a text with no Hangul
    is fine when it is quoted whole."""
    kind = language_problem(text, problem)
    if kind == "SCRIPT":
        quoted = all(quotes.has(run) for run in _FOREIGN_RUN.findall(text))
        return None if quoted else kind
    if kind == "LANGUAGE":
        return None if quotes.has(text.strip()) else kind
    return None
