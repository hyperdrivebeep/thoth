"""Chinese and Japanese terms for the CJK retrieval lanes (policy 6.0.0, shadow only).

The earlier lanes cut a text into Latin/digit words and Hangul, so a Han or kana run produces no
term at all (BM25 lanes) or one term for the whole clause (substring lane). These terms cover
exactly what those lanes skip and nothing they already read:

- A run is a maximal stretch of Han characters, hiragana and katakana (the katakana middle dot is
  punctuation and ends a run; the prolonged sound mark stays inside one).
- A run of two or more characters gives every overlapping character bigram. A run of one
  character gives that character.
- Text is folded first (NFKC, case-folded), so half-width katakana and compatibility ideographs
  join their ordinary forms. Simplified and Traditional Chinese are different characters and stay
  different; no stop list is applied.

A lane adds these to the terms its earlier version reads; Latin, digits and Hangul are read by
the earlier rule unchanged.
"""

import re
from functools import lru_cache

from thoth.domain.lane_scoring import bigram_terms, identifier_terms
from thoth.domain.query_identifiers import fold

CJK_VERSION = "cjk-bigram:1.0.0"
_RUN = re.compile(
    "[\u3005-\u3007\u3040-\u309f\u30a1-\u30fa\u30fc-\u30ff\u31f0-\u31ff"
    "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0002fa1f]+"
)


@lru_cache(maxsize=65536)
def cjk_terms(text: str) -> tuple[str, ...]:
    """The bigrams (single characters for a one-character run) of every Han/kana run of a text."""
    terms: list[str] = []
    for run in _RUN.findall(fold(text)):
        terms.extend([run] if len(run) == 1 else (run[i : i + 2] for i in range(len(run) - 1)))
    return tuple(terms)


def cjk_size(text: str) -> int:
    """A text's length for the short-line rule: a Han/kana character counts as three, since one
    carries about as much as three Latin letters (the 24-character floor was set for Latin text)."""
    return len(text) + 2 * sum(len(run) for run in _RUN.findall(text))


def cjk_identifier_terms(text: str) -> tuple[str, ...]:
    """The grouped lane's terms of a text plus its CJK terms."""
    return (*identifier_terms(text), *cjk_terms(text))


def cjk_bigram_terms(text: str) -> tuple[str, ...]:
    """The bigram lane's terms of a text plus its CJK terms."""
    return (*bigram_terms(text), *cjk_terms(text))
