"""Local single-text encoding contract; implementations arrive with the optional runtime."""

from typing import Literal, Protocol

from thoth.domain.retrieval_embedding import DenseModelPin


class LocalEmbeddingPort(Protocol):
    def encode_one(
        self,
        text: str,
        *,
        role: Literal["QUERY", "PASSAGE"],
        model: DenseModelPin,
    ) -> tuple[float, ...]: ...
