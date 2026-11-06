"""Bound, precomputed dense evidence. No encoder, files or provider I/O in selection."""

import hashlib
import json
from collections.abc import Iterable
from decimal import Decimal
from operator import is_
from typing import Literal

from pydantic import Field, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.canonical import domain_digest, normalize_text
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.ids import Sha256

GRANITE_MODEL = "ibm-granite/granite-embedding-97m-multilingual-r2"
GRANITE_REVISION = "835ad14087e140460703cf0fae09f97d469d65c2"
GRANITE_ONNX_SHA = "a6022dd8220ea6f6595562a1328ee216f4a94faa55362f2f4747c80f1e78772e"
# Identity of the installed file bundle (the packaged manifest). It names how the model files are
# meant to be used and must not change with code-side preprocessing, or the installed folder
# (named by the manifest digest) would no longer be found and the model would need a reinstall.
GRANITE_ASSET_PREPROCESSING = "granite-cls-l2-batch1-question-only:1.0.0"
# Identity of what this code does to a passage before the model sees it. Raise the version
# whenever the preprocessing changes, so vectors made another way are never mixed in.
# 1.1.0 kept only the first PASSAGE_WINDOW_TOKENS tokens. 1.2.0 encodes a long passage as several
# overlapping windows; "win510o128m8" is WINDOW_CONTENT_TOKENS, WINDOW_OVERLAP_TOKENS and
# MAX_PASSAGE_WINDOWS, and "cap512" is the length of one window with its two special tokens
# (onnx_encoder.PASSAGE_TOKEN_CAP). A test keeps the name equal to these numbers.
GRANITE_PREPROCESSING_V1 = "granite-cls-l2-batch1-question-only:1.0.0"
GRANITE_PREPROCESSING_V11 = "granite-cls-l2-batch1-question-only-cap512:1.1.0"
GRANITE_PREPROCESSING = "granite-cls-l2-batch1-question-only-win510o128m8-cap512:1.2.0"

# Window geometry. Content tokens exclude the opening and closing special tokens of a window.
PASSAGE_WINDOW_TOKENS = 512
WINDOW_CONTENT_TOKENS = 510
WINDOW_OVERLAP_TOKENS = 128
MAX_PASSAGE_WINDOWS = 8


def window_bounds(source_tokens: int) -> tuple[tuple[int, int], ...]:
    """Content-token slices [start, end) of a passage with source_tokens tokens (specials included).

    A passage that fits one window has one slice and is encoded whole. Window 0 always starts at
    the first content token and holds the first WINDOW_CONTENT_TOKENS, which is exactly what the
    1.1.0 cap kept. Later windows start WINDOW_CONTENT_TOKENS - WINDOW_OVERLAP_TOKENS after the
    previous one. At most MAX_PASSAGE_WINDOWS windows are made; content past the last one is cut.
    """
    if source_tokens < 2:
        raise ValueError("EMBEDDING_SOURCE_TOKENS_INVALID")
    content = source_tokens - 2
    step = WINDOW_CONTENT_TOKENS - WINDOW_OVERLAP_TOKENS
    bounds: list[tuple[int, int]] = []
    start = 0
    while len(bounds) < MAX_PASSAGE_WINDOWS:
        end = min(start + WINDOW_CONTENT_TOKENS, content)
        bounds.append((start, end))
        if end >= content:
            break
        start += step
    return tuple(bounds)


def window_count(source_tokens: int | None) -> int:
    """Windows of a passage whose first window reported source_tokens; None means a single one."""
    if source_tokens is None or source_tokens <= PASSAGE_WINDOW_TOKENS:
        return 1
    return len(window_bounds(source_tokens))


def window_cut(source_tokens: int) -> bool:
    """True when content past the last window is not encoded by any window."""
    return window_bounds(source_tokens)[-1][1] < source_tokens - 2


class DenseModelPin(DomainModel):
    model_id: Literal["ibm-granite/granite-embedding-97m-multilingual-r2"] = GRANITE_MODEL
    revision: Literal["835ad14087e140460703cf0fae09f97d469d65c2"] = GRANITE_REVISION
    onnx_sha256: Literal["a6022dd8220ea6f6595562a1328ee216f4a94faa55362f2f4747c80f1e78772e"] = (
        GRANITE_ONNX_SHA
    )
    # The earlier value stays readable so rows written before the cap still load; they are
    # recognised as outdated by comparing with DenseModelPin(), never rewritten in place.
    preprocessing: Literal[
        "granite-cls-l2-batch1-question-only:1.0.0",
        "granite-cls-l2-batch1-question-only-cap512:1.1.0",
        "granite-cls-l2-batch1-question-only-win510o128m8-cap512:1.2.0",
    ] = GRANITE_PREPROCESSING


class DenseScore(DomainModel):
    span_id: str = Field(min_length=1, max_length=160)
    score: Decimal = Field(allow_inf_nan=False, ge=Decimal("-1.00001"), le=Decimal("1.00001"))


DenseUnavailableReason = Literal[
    "MODEL_NOT_INSTALLED",
    "MODEL_HASH_MISMATCH",
    "EMBEDDING_DEPENDENCY_UNAVAILABLE",
    "INDEX_PENDING",
    "INDEX_STALE",
    "INDEX_INCOMPLETE",
    "INDEX_TOO_LARGE",
    "QUERY_TOO_LONG",
    "EMBEDDING_TIMEOUT",
    "EMBEDDING_FAILED",
    "EMBEDDING_NOT_PREPARED",
]


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
    unavailable_reason: DenseUnavailableReason | None = None

    @model_validator(mode="after")
    def _unique_scores(self) -> "DenseMaterial":
        if len({s.span_id for s in self.scores}) != len(self.scores):
            raise ValueError("DENSE_DUPLICATE_SPAN")
        if self.unavailable_reason is not None and self.scores:
            raise ValueError("DENSE_UNAVAILABLE_WITH_SCORES")
        return self

    @classmethod
    def prepared(
        cls,
        *,
        project_id: str,
        question_sha256: str,
        source_digest: str,
        scores: tuple[DenseScore, ...] = (),
        unavailable_reason: DenseUnavailableReason | None = None,
    ) -> "DenseMaterial":
        """A material whose producer has already guaranteed what validation would check.

        The producer (see dense_material) builds one score per distinct eligible span, each rounded
        to nine places inside [-1, 1] and at most 20,000 of them, so the per-score validation that
        costs about 20 ms on a large frame is skipped. Anything outside a producer goes through
        the normal constructor.
        """
        return cls.model_construct(
            project_id=project_id,
            question_sha256=question_sha256,
            source_digest=source_digest,
            scores=scores,
            unavailable_reason=unavailable_reason,
        )


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


def _nfc(value: str) -> str:
    """normalize_text, skipping the call for ASCII, which NFC never changes."""
    return value if value.isascii() else normalize_text(value)


def _canonical_json(value: object) -> bytes:
    """The bytes canonical_payload gives a structure of dicts, lists and NFC-normalized strings."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


# The digest of the last frame, so the producer that bound it and dense_rank that checks it do
# the work once. A hit needs the very same span objects in the same order, which makes the digest
# the same by construction; a different frame, even an equal one, is simply computed again.
_source_memo: tuple[tuple[EvidenceSpan, ...], str] | None = None


def remembered_frame() -> tuple[EvidenceSpan, ...] | None:
    """The frame whose digest is remembered, for tests and diagnostics."""
    return None if _source_memo is None else _source_memo[0]


def forget_remembered_frame() -> None:
    global _source_memo
    _source_memo = None


def dense_source_digest(evidence: tuple[EvidenceSpan, ...]) -> str:
    global _source_memo
    memo = _source_memo
    if memo is not None and len(memo[0]) == len(evidence) and all(map(is_, memo[0], evidence)):
        return memo[1]
    rows = [
        [
            _nfc(s.project_id),
            _nfc(s.artifact_id),
            _nfc(s.source_version_id),
            _nfc(s.span_id),
            _nfc(s.text_sha256),
        ]
        for s in evidence
    ]
    digest = domain_digest("DENSE_SOURCE", "1.0.0", _canonical_json({"sources": rows}))
    _source_memo = (evidence, digest)
    return digest


def dense_material_digest(material: DenseMaterial) -> str:
    """Digest of a material: the bytes canonical_payload(material) gives, built directly."""
    scores: Iterable[dict[str, str]] = (
        {"score": format(s.score, "f"), "span_id": _nfc(s.span_id)} for s in material.scores
    )
    payload = {
        "model_id": _nfc(material.model_id),
        "onnx_sha256": _nfc(material.onnx_sha256),
        "preprocessing": _nfc(material.preprocessing),
        "project_id": _nfc(material.project_id),
        "question_sha256": _nfc(material.question_sha256),
        "revision": _nfc(material.revision),
        "scores": list(scores),
        "source_digest": _nfc(material.source_digest),
        "unavailable_reason": material.unavailable_reason,
    }
    return domain_digest("DENSE_MATERIAL", "1.0.0", _canonical_json(payload))


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
