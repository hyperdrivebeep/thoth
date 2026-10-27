"""Words the retrieval lanes share: the length rule and the Korean function words."""

import re

MIN_TERM = 3
KOREAN_PAIR = re.compile(r"[가-힣]{2}")
# Only the newly admitted two-syllable words are filtered. These grammatical/context
# words add little topic information; no domain words or scenario-specific exceptions.
KOREAN_FUNCTION_WORDS = frozenset(
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
