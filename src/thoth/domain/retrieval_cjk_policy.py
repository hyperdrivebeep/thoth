"""Retrieval policy 6.0.0: one CJK lane, for shadow evaluation only. It is never a default."""

from typing import Literal, get_args

from pydantic import Field

from thoth.domain.base import DomainModel

type CjkLaneId = Literal["LEXICAL_SUBSTRING_CJK", "GROUPED_BM25_CJK", "CHAR_BIGRAM_CJK"]
CJK_LANE_IDS: tuple[str, ...] = get_args(CjkLaneId.__value__)


class RetrievalCjkPolicy(DomainModel):
    """The earlier lane of the same name plus Han/kana bigrams (see retrieval_cjk).

    The lane has no default: no lane is chosen before it is measured. The baseline stays 2.0.0, and
    2.0.0 to 5.0.0 are unchanged, so their serialization and digests are too.
    """

    kind: Literal["RETRIEVAL_POLICY"] = "RETRIEVAL_POLICY"
    version: Literal["6.0.0"] = "6.0.0"
    max_spans: int = Field(default=160, ge=1, le=160)
    character_budget: int = Field(default=50000, ge=1, le=50000)
    exposure: Literal["SHADOW_ONLY"] = "SHADOW_ONLY"
    lane_id: CjkLaneId
    lane_version: Literal["1.0.0"] = "1.0.0"
