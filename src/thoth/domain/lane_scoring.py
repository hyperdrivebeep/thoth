"""Scoring the ranked lanes share: positive-IDF BM25 and the two ways of cutting text into terms.

BM25_VERSION names the formula and its constants; a lane whose scoring changes is registered under
a new lane version. The constants are not policy fields.

- score(unit, term) = idf(term) * tf * (k1 + 1) / (tf + k1 * (1 - b + b * length / average length))
- idf(term) = ln(1 + (N - n + 0.5) / (n + 0.5)), never negative; N is the number of units the
  request may read (its eligible groups or spans) and n how many of them hold the term.
- Query terms are used once each. Units with no matching term score 0 and are not ranked.
"""

import math
import re
from collections import Counter
from collections.abc import Sequence
from functools import lru_cache

from thoth.domain.query_identifiers import fold, scan_query
from thoth.domain.retrieval_terms import KOREAN_FUNCTION_WORDS, KOREAN_PAIR, MIN_TERM

BM25_K1 = 1.2
BM25_B = 0.75
BM25_VERSION = "bm25:k1=1.2;b=0.75;idf=ln(1+(N-n+0.5)/(n+0.5))"
BIGRAM_VERSION = "char-bigram:1.0.0"
_ROUND = 12
_WORD = re.compile(r"[a-z0-9_]+(?:[-./@][a-z0-9_]+)*|[가-힣]+")


def bm25_scores(docs: Sequence[Sequence[str]], terms: Sequence[str]) -> list[float]:
    """One score per document; N and the document frequencies come from `docs` as given."""
    unique = tuple(dict.fromkeys(terms))
    count = len(docs)
    if not count or not unique:
        return [0.0] * count
    counters = [Counter(doc) for doc in docs]
    average = sum(len(doc) for doc in docs) / count or 1.0
    scores = [0.0] * count
    for term in unique:
        frequency = sum(1 for c in counters if c[term])
        idf = math.log(1 + (count - frequency + 0.5) / (frequency + 0.5))
        for index, counter in enumerate(counters):
            tf = counter[term]
            if tf:
                norm = 1 - BM25_B + BM25_B * len(docs[index]) / average
                scores[index] += idf * tf * (BM25_K1 + 1) / (tf + BM25_K1 * norm)
    return [round(score, _ROUND) for score in scores]


@lru_cache(maxsize=65536)
def identifier_terms(text: str) -> tuple[str, ...]:
    """Terms of a text for grouped scoring: every form of each identifier the scanner fixed, then
    the pieces it left over (Latin and digit pieces of two characters or more, Hangul by the
    earlier two-syllable rule)."""
    fields = scan_query(text)
    terms: list[str] = [needle for i in fields.identifiers for needle in i.needles]
    for token in fields.split_tokens:
        folded = fold(token.text)
        if token.script == "HANGUL":
            keep = len(folded) >= MIN_TERM or (
                KOREAN_PAIR.fullmatch(folded) is not None and folded not in KOREAN_FUNCTION_WORDS
            )
        else:
            keep = len(folded) >= 2
        if keep:
            terms.append(folded)
    return tuple(terms)


@lru_cache(maxsize=65536)
def bigram_terms(text: str) -> tuple[str, ...]:
    """Hangul syllable bigrams (function words dropped) and symbol-preserving Latin/digit words."""
    terms: list[str] = []
    for match in _WORD.finditer(fold(text)):
        word = match.group()
        if "가" <= word[0] <= "힣":
            terms.extend(
                bigram
                for bigram in (word[i : i + 2] for i in range(len(word) - 1))
                if bigram not in KOREAN_FUNCTION_WORDS
            )
        elif len(word) >= 2:
            terms.append(word)
    return tuple(terms)
