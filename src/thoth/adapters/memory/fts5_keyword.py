from __future__ import annotations

import re

from thoth.ports.memory import MemoryRerankerPort

_TOKEN = re.compile(r"[0-9A-Za-z가-힣_]{3,}")


class KeywordMemoryReranker(MemoryRerankerPort):
    def rank(self, query: str, candidates: tuple[tuple[str, str], ...]) -> tuple[str, ...]:
        terms = {item.group(0).casefold() for item in _TOKEN.finditer(query)}
        scored = (
            (
                len(terms.intersection(item.group(0).casefold() for item in _TOKEN.finditer(text))),
                identifier,
            )
            for identifier, text in candidates
        )
        return tuple(
            identifier
            for _score, identifier in sorted(scored, key=lambda item: (-item[0], item[1]))
        )
