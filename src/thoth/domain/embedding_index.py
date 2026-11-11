"""Operational, derived embedding state and fixed resource limits, never source truth."""

import hashlib
import math
import struct
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.evidence import EvidenceSpan, connected_retrieval_spans
from thoth.domain.ids import Sha256
from thoth.domain.retrieval_embedding import MAX_PASSAGE_WINDOWS, DenseModelPin

# Why a job was cancelled because the service shut down: not a person's cancellation, so the
# background indexer takes it up again at the next start.
SERVICE_CLOSED_REASON = "SERVICE_CLOSED"


class EmbeddingBudget(DomainModel):
    version: Literal["embedding-budget:1.0.0"] = "embedding-budget:1.0.0"
    query_timeout_ms: int = Field(default=2000, ge=1, le=2000)
    span_timeout_ms: int = Field(default=10000, ge=1, le=10000)
    job_timeout_ms: int = Field(default=600000, ge=1, le=600000)
    lease_ms: int = Field(default=60000, ge=1, le=60000)
    query_queue: int = Field(default=16, ge=1, le=16)
    commit_limit_bytes: int = Field(
        default=512 * 1024 * 1024, ge=16 * 1024 * 1024, le=512 * 1024 * 1024
    )
    max_input_bytes: int = Field(default=1024 * 1024, ge=1, le=1024 * 1024)
    workers: Literal[1] = 1
    batch_size: Literal[1] = 1
    automatic_retries: Literal[0] = 0


class EmbeddingCapability(DomainModel):
    capability_id: Literal["LOCAL_EMBEDDING_PROCESS_V1"] = "LOCAL_EMBEDDING_PROCESS_V1"
    batch_size: Literal[1] = 1
    process_tree: Literal["WINDOWS_JOB", "POSIX_SESSION"]
    memory_limit: Literal["COMMIT", "ADDRESS_SPACE"]
    remote_model: Literal[False] = False


class EmbeddingIndexConfig(DomainModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    project_id: str
    enabled: bool = False
    generation: int = Field(default=0, ge=0)
    actor_ref: str
    model: DenseModelPin = DenseModelPin()
    budget: EmbeddingBudget = EmbeddingBudget()


class EmbeddingSourceRef(DomainModel):
    span_id: str
    text_sha256: Sha256
    authority: str
    cutoff: str


class EmbeddingIntent(DomainModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    project_id: str
    artifact_id: str
    source_version_id: str
    source_sha256: Sha256
    refs: tuple[EmbeddingSourceRef, ...]
    model: DenseModelPin
    config_generation: int = Field(ge=1)

    @model_validator(mode="after")
    def unique_refs(self) -> "EmbeddingIntent":
        ids = tuple(ref.span_id for ref in self.refs)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("EMBEDDING_REFS_NOT_UNIQUE_SORTED")
        return self

    @property
    def digest(self) -> str:
        return domain_digest("EMBEDDING_INTENT", "1.0.0", canonical_payload(self))


class EmbeddingJob(DomainModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    job_id: str
    intent: EmbeddingIntent
    state: Literal["PENDING", "RUNNING", "READY", "FAILED", "CANCELLED", "STALE"]
    generation: int = Field(ge=1)
    owner: str | None = None
    lease_until_ms: int | None = None
    deadline_ms: int | None = None
    reason: str | None = None
    manifest_digest: str | None = None

    @model_validator(mode="after")
    def state_contract(self) -> "EmbeddingJob":
        if self.job_id != self.intent.digest:
            raise ValueError("EMBEDDING_JOB_IDENTITY_MISMATCH")
        if self.state == "RUNNING" and (
            not self.owner or self.lease_until_ms is None or self.deadline_ms is None
        ):
            raise ValueError("EMBEDDING_RUNNING_LEASE_MISSING")
        if self.state == "READY" and not self.manifest_digest:
            raise ValueError("EMBEDDING_READY_MANIFEST_MISSING")
        return self


class EmbeddingIndexError(ValueError):
    pass


def source_refs(spans: tuple[EvidenceSpan, ...]) -> tuple[EmbeddingSourceRef, ...]:
    return tuple(
        EmbeddingSourceRef(
            span_id=s.span_id,
            text_sha256=s.text_sha256,
            authority=s.authority_state.value,
            cutoff=s.cutoff_state.value,
        )
        for s in sorted(connected_retrieval_spans(spans), key=lambda s: s.span_id)
    )


def embedding_key(text_sha256: str, model: DenseModelPin) -> str:
    return domain_digest(
        "EMBEDDING_CONTENT_KEY",
        "1.0.0",
        canonical_payload(
            {
                "text_sha256": text_sha256,
                "model": model,
                "role": "PASSAGE",
                "dtype": "float32-le",
                "dimensions": 384,
            }
        ),
    )


def window_key(text_sha256: str, model: DenseModelPin, window: int) -> str:
    """Content key of window 1 or later of a long passage.

    Window 0 keeps embedding_key, so a passage that fits one window is stored exactly as before.
    """
    if not 1 <= window < MAX_PASSAGE_WINDOWS:
        raise EmbeddingIndexError("EMBEDDING_WINDOW_INVALID")
    return domain_digest(
        "EMBEDDING_WINDOW_KEY",
        "1.0.0",
        canonical_payload(
            {
                "text_sha256": text_sha256,
                "model": model,
                "role": "PASSAGE",
                "dtype": "float32-le",
                "dimensions": 384,
                "window": window,
            }
        ),
    )


def content_key(text_sha256: str, model: DenseModelPin, window: int = 0) -> str:
    return (
        embedding_key(text_sha256, model) if window == 0 else window_key(text_sha256, model, window)
    )


@dataclass(frozen=True)
class EmbeddingVector:
    data: bytes
    # Whether some of the passage was left out of every window, and how many tokens the passage
    # had when it needed more than one window or was cut. None means not recorded (a vector made
    # before this was kept, or by a worker that does not report it); False means every token
    # reached the model. source_tokens is what tells a reader how many windows to expect.
    # They describe how the vector was made, not which vector it is, so equality ignores them.
    truncated: bool | None = field(default=None, compare=False)
    source_tokens: int | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if len(self.data) != 384 * 4:
            raise EmbeddingIndexError("EMBEDDING_VECTOR_DIMENSION")
        values = struct.unpack("<384f", self.data)
        if not all(math.isfinite(v) for v in values):
            raise EmbeddingIndexError("EMBEDDING_VECTOR_NONFINITE")
        if abs(math.sqrt(sum(v * v for v in values)) - 1) > 1e-4:
            raise EmbeddingIndexError("EMBEDDING_VECTOR_NOT_NORMALIZED")
        if self.source_tokens is not None and self.source_tokens < 1:
            raise EmbeddingIndexError("EMBEDDING_SOURCE_TOKENS_INVALID")

    @classmethod
    def from_values(cls, values: tuple[float, ...]) -> "EmbeddingVector":
        if len(values) != 384:
            raise EmbeddingIndexError("EMBEDDING_VECTOR_DIMENSION")
        return cls(struct.pack("<384f", *values))

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    def values(self) -> tuple[float, ...]:
        return struct.unpack("<384f", self.data)


def max_window_score(query: EmbeddingVector, windows: Sequence[EmbeddingVector]) -> Decimal:
    """MaxP: a passage scores as its best window. Vectors are unit length, so a dot product is
    the cosine. The result is rounded to nine places and fits DenseScore."""
    if not windows:
        raise EmbeddingIndexError("EMBEDDING_WINDOWS_MISSING")
    q = query.values()
    best = max(sum(a * b for a, b in zip(q, w.values(), strict=True)) for w in windows)
    return Decimal(f"{max(-1.0, min(1.0, best)):.9f}")
