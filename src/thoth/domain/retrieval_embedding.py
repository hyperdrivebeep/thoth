"""Bound, precomputed dense evidence. No encoder, files or provider I/O in selection."""

import hashlib
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.ids import Sha256

GRANITE_MODEL = "ibm-granite/granite-embedding-97m-multilingual-r2"
GRANITE_REVISION = "835ad14087e140460703cf0fae09f97d469d65c2"
GRANITE_ONNX_SHA = "a6022dd8220ea6f6595562a1328ee216f4a94faa55362f2f4747c80f1e78772e"
GRANITE_PREPROCESSING = "granite-cls-l2-batch1-question-only:1.0.0"


class DenseModelPin(DomainModel):
    model_id: Literal["ibm-granite/granite-embedding-97m-multilingual-r2"] = GRANITE_MODEL
    revision: Literal["835ad14087e140460703cf0fae09f97d469d65c2"] = GRANITE_REVISION
    onnx_sha256: Literal["a6022dd8220ea6f6595562a1328ee216f4a94faa55362f2f4747c80f1e78772e"] = (
        GRANITE_ONNX_SHA
    )
    preprocessing: Literal["granite-cls-l2-batch1-question-only:1.0.0"] = GRANITE_PREPROCESSING


class DenseScore(DomainModel):
    span_id: str = Field(min_length=1, max_length=160)
    score: Decimal = Field(allow_inf_nan=False, ge=Decimal("-1.00001"), le=Decimal("1.00001"))


class DenseMaterial(DomainModel):
    """Host-prepared scores, bound to the original question and exact eligible source set.

    An evaluator validates the binding, not a claim that a host really ran the model.
    Production producers must separately verify the model and index before providing scores.
    """

    project_id: str = Field(min_length=1, max_length=160)
    question_sha256: Sha256
    source_digest: Sha256
    model_id: str = GRANITE_MODEL
    revision: str = GRANITE_REVISION
    onnx_sha256: Sha256 = GRANITE_ONNX_SHA
    preprocessing: str = GRANITE_PREPROCESSING
    scores: tuple[DenseScore, ...] = Field(default=(), max_length=20_000)
    unavailable_reason: (
        Literal[
            "MODEL_NOT_INSTALLED",
            "MODEL_HASH_MISMATCH",
            "EMBEDDING_DEPENDENCY_UNAVAILABLE",
            "INDEX_PENDING",
            "INDEX_STALE",
            "INDEX_INCOMPLETE",
            "QUERY_TOO_LONG",
            "EMBEDDING_TIMEOUT",
            "EMBEDDING_FAILED",
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def _unique_scores(self) -> "DenseMaterial":
        if len({s.span_id for s in self.scores}) != len(self.scores):
            raise ValueError("DENSE_DUPLICATE_SPAN")
        if self.unavailable_reason is not None and self.scores:
            raise ValueError("DENSE_UNAVAILABLE_WITH_SCORES")
        return self


class RoutingObservation(DomainModel):
    artifact_count: int = Field(ge=0)
    route: Literal["LEXICAL", "FULL_CATALOG", "RRF_LEX_GB_DENSE", "RRF_LEX_GB"]
    dense_used: bool
    fallback_reason: str | None = None
    dense_material_digest: str | None = None


class DenseUnavailable(ValueError):
    pass


def dense_question_digest(question: str) -> str:
    """Expanded queries intentionally never participate in dense encoding or its binding."""
    return hashlib.sha256(question.encode("utf-8")).hexdigest()


def dense_source_digest(evidence: tuple[EvidenceSpan, ...]) -> str:
    return domain_digest(
        "DENSE_SOURCE",
        "1.0.0",
        canonical_payload(
            {
                "sources": tuple(
                    (s.project_id, s.artifact_id, s.source_version_id, s.span_id, s.text_sha256)
                    for s in evidence
                )
            }
        ),
    )


def dense_rank(
    question: str,
    evidence: tuple[EvidenceSpan, ...],
    material: DenseMaterial | None,
) -> tuple[EvidenceSpan, ...]:
    if material is None:
        raise DenseUnavailable("DENSE_MATERIAL_MISSING")
    projects = {s.project_id for s in evidence}
    if projects != {material.project_id}:
        raise ValueError("DENSE_PROJECT_SCOPE_MISMATCH")
    if material.question_sha256 != dense_question_digest(question):
        raise DenseUnavailable("DENSE_QUESTION_MISMATCH")
    if material.source_digest != dense_source_digest(evidence):
        raise DenseUnavailable("INDEX_STALE")
    if (material.model_id, material.revision, material.onnx_sha256, material.preprocessing) != (
        GRANITE_MODEL,
        GRANITE_REVISION,
        GRANITE_ONNX_SHA,
        GRANITE_PREPROCESSING,
    ):
        raise DenseUnavailable("MODEL_HASH_MISMATCH")
    if material.unavailable_reason is not None:
        raise DenseUnavailable(material.unavailable_reason)
    by_id = {s.span_id: s for s in evidence}
    if len(by_id) != len(evidence) or set(by_id) != {s.span_id for s in material.scores}:
        raise DenseUnavailable("INDEX_INCOMPLETE")
    return tuple(
        by_id[s.span_id] for s in sorted(material.scores, key=lambda s: (-s.score, s.span_id))
    )
