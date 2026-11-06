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


class WindowedEmbeddingPort(Protocol):
    """An encoder that can encode one window of a long passage (window 0 is the whole input of a
    passage that fits one window). A plain LocalEmbeddingPort is asked for window 0 only."""

    def encode_one(
        self,
        text: str,
        *,
        role: Literal["QUERY", "PASSAGE"],
        model: DenseModelPin,
        window: int = 0,
    ) -> tuple[float, ...]: ...
