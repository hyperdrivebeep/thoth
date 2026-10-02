"""The one check for memory text that looks like an instruction or a secret."""

from __future__ import annotations

import re

REDACTED_MEMORY = "[REDACTED_QUARANTINED_MEMORY]"

_UNSAFE = re.compile(
    r"ignore\s+(?:all|previous)|system\s+prompt|developer\s+message|"
    r"이전\s*명령.*무시|(?:api[_-]?key|secret|password|token)\s*[:=]\s*[^\s]{8,}",
    re.IGNORECASE,
)


def memory_text_is_unsafe(text: str) -> bool:
    """True for text the memory review quarantines, and for text already redacted for it."""

    return text == REDACTED_MEMORY or _UNSAFE.search(text) is not None
