"""The three CJK retrieval lanes of policy 6.0.0 (shadow only).

Each is the earlier lane of the same name (substring, grouped BM25, character bigram) with the
Han/kana terms of retrieval_cjk added to the terms it reads. The earlier lanes are not touched;
these register under their own ids when this module is first imported, which only a 6.0.0 policy
causes.
"""

from thoth.domain.behavior_policy import RetrievalCjkPolicy
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.lane_scoring import BIGRAM_VERSION, BM25_VERSION
from thoth.domain.query_identifiers import SCANNER_VERSION
from thoth.domain.retrieval_cjk import (
    CJK_VERSION,
    cjk_bigram_terms,
    cjk_identifier_terms,
    cjk_size,
    cjk_terms,
)
from thoth.domain.retrieval_fusion import Chunk
from thoth.domain.retrieval_lane import (
    LaneSelection,
    RetrievalRequest,
    bigram_ranked,
    grouped_chunks,
    lexical_ranked,
    register_lane,
    search_terms,
    select_with_lane,
)
from thoth.domain.structure_groups import GROUPING_VERSION

LEXICAL_SUBSTRING_CJK = "LEXICAL_SUBSTRING_CJK"
GROUPED_BM25_CJK = "GROUPED_BM25_CJK"
CHAR_BIGRAM_CJK = "CHAR_BIGRAM_CJK"


def _texts(request: RetrievalRequest) -> tuple[str, ...]:
    return (request.question, *request.queries)


def lexical_substring_cjk_v1(
    request: RetrievalRequest, eligible: tuple[EvidenceSpan, ...]
) -> tuple[EvidenceSpan, ...]:
    """Substring term coverage over the earlier terms and the question's CJK bigrams."""
    text = " ".join(_texts(request))
    return lexical_ranked(request, eligible, search_terms(text) | set(cjk_terms(text)), cjk_size)


def grouped_bm25_cjk_chunks_v1(
    request: RetrievalRequest, eligible: tuple[EvidenceSpan, ...]
) -> tuple[Chunk, ...]:
    terms = [t for text in _texts(request) for t in cjk_identifier_terms(text)]
    return grouped_chunks(request, eligible, terms, cjk_identifier_terms)


def grouped_bm25_cjk_v1(
    request: RetrievalRequest, eligible: tuple[EvidenceSpan, ...]
) -> tuple[EvidenceSpan, ...]:
    return tuple(span for chunk in grouped_bm25_cjk_chunks_v1(request, eligible) for span in chunk)


def char_bigram_cjk_v1(
    request: RetrievalRequest, eligible: tuple[EvidenceSpan, ...]
) -> tuple[EvidenceSpan, ...]:
    return bigram_ranked(request, eligible, cjk_bigram_terms)


register_lane(LEXICAL_SUBSTRING_CJK, "1.0.0", lexical_substring_cjk_v1, (("terms", CJK_VERSION),))
register_lane(
    GROUPED_BM25_CJK,
    "1.0.0",
    grouped_bm25_cjk_v1,
    (
        ("grouping", GROUPING_VERSION),
        ("scoring", BM25_VERSION),
        ("scanner", SCANNER_VERSION),
        ("terms", CJK_VERSION),
    ),
    grouped_bm25_cjk_chunks_v1,
)
register_lane(
    CHAR_BIGRAM_CJK,
    "1.0.0",
    char_bigram_cjk_v1,
    (("scoring", BM25_VERSION), ("terms", BIGRAM_VERSION), ("cjk", CJK_VERSION)),
)


def select_cjk(request: RetrievalRequest, policy: RetrievalCjkPolicy) -> LaneSelection:
    """The frame of every lane around the CJK lane the policy names."""
    return select_with_lane(request, policy, policy.lane_id)
