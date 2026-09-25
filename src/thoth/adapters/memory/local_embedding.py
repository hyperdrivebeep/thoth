from __future__ import annotations

import hashlib

from thoth.ports.memory import MemoryEmbeddingPort


class LocalMemoryEmbedding(MemoryEmbeddingPort):
    def __init__(self, *, dimensions: int = 64) -> None:
        if dimensions < 8:
            raise ValueError("memory embedding dimensions must be at least 8")
        self._dimensions = dimensions

    def embed(self, text: str) -> tuple[int, ...]:
        values = [0] * self._dimensions
        normalized = " ".join(text.casefold().split())
        for index in range(max(1, len(normalized) - 2)):
            gram = normalized[index : index + 3]
            digest = hashlib.sha256(gram.encode()).digest()
            slot = int.from_bytes(digest[:2], "big") % self._dimensions
            values[slot] += 1 if digest[2] % 2 == 0 else -1
        return tuple(values)
