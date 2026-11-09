"""Encoders that a test worker's child process imports by name.

The child is limited to 512 MiB of commit, so this module must stay light: it imports no test
module, parser or spreadsheet library (openpyxl imports numpy when numpy is installed, which
overflows that limit in an environment with the local-retrieval extra). Keep every factory a
ProcessEmbeddingWorker test names here, and keep this file free of heavy imports.
"""

import hashlib
import os
import socket
import time
from typing import Literal

from thoth.adapters.embeddings.onnx_encoder import PASSAGE_TOKEN_CAP
from thoth.domain.model_asset import EmbeddingAssetDescriptor
from thoth.domain.retrieval_embedding import DenseModelPin, window_cut

MODULE = "tests.embedding_child_encoders"


def unit_vector(text: str) -> tuple[float, ...]:
    position = hashlib.sha256(text.encode()).digest()[0]
    return tuple(1.0 if i == position else 0.0 for i in range(384))


class ChildEncoder:
    def encode_one(
        self, text: str, *, role: Literal["QUERY", "PASSAGE"], model: DenseModelPin
    ) -> tuple[float, ...]:
        if text.startswith("sleep:"):
            time.sleep(float(text.split(":")[1]))
        if text == "crash":
            os._exit(23)
        if text == "bad":
            return (0.0,) * 384
        if text == "network":
            socket.create_connection(("127.0.0.1", 9))
        if text == "memory":
            # Deliberately exceed the approved Job commit cap in this owned child only.
            bytearray(600 * 1024 * 1024)
        return unit_vector(text)


class DescriptorEncoder(ChildEncoder):
    def __init__(self, descriptor: "EmbeddingAssetDescriptor") -> None:
        if descriptor.root == "synthetic-slow-load":
            time.sleep(30)
        if descriptor.root == "synthetic-bad-hash":
            raise ValueError("MODEL_HASH_MISMATCH")
        if descriptor.root == "synthetic-load-crash":
            os._exit(23)
        if descriptor.root == "synthetic-malformed-frame":
            # Bypass Python's redirected stdout, like native-code diagnostics.
            os.write(1, b"not a JSON frame\n")


class ReportingEncoder:
    """Child-side stand-in for the real encoder's reporting attributes."""

    def __init__(self) -> None:
        self.last_truncated = False
        self.last_source_tokens: int | None = None

    def encode_one(
        self, text: str, *, role: Literal["QUERY", "PASSAGE"], model: DenseModelPin
    ) -> tuple[float, ...]:
        count = int(text.split(":")[1]) if text.startswith("tokens:") else 10
        self.last_source_tokens = count
        self.last_truncated = role == "PASSAGE" and count > PASSAGE_TOKEN_CAP
        return unit_vector(text)


class WindowedEncoder:
    """Child-side stand-in: 'tokens:N' reports N source tokens, each window has its own vector."""

    def __init__(self) -> None:
        self.last_truncated = False
        self.last_source_tokens: int | None = None

    def encode_one(
        self,
        text: str,
        *,
        role: Literal["QUERY", "PASSAGE"],
        model: DenseModelPin,
        window: int = 0,
    ) -> tuple[float, ...]:
        count = int(text.split(":")[1]) if text.startswith("tokens:") else 10
        self.last_source_tokens = count
        self.last_truncated = role == "PASSAGE" and count > 2 and window_cut(count)
        return unit_vector(f"{text}#{window}")


class CappedEncoder:
    """Child-side stand-in: flags passages whose text starts with 'long'."""

    def __init__(self) -> None:
        self.last_truncated = False

    def encode_one(
        self, text: str, *, role: Literal["QUERY", "PASSAGE"], model: DenseModelPin
    ) -> tuple[float, ...]:
        self.last_truncated = role == "PASSAGE" and text.startswith("long")
        return unit_vector(text)
