"""Pure record construction for the full-project memory promotion (96a).

These functions only turn values the service has already read into reviews, flags, digests and
the public promotion result. They read nothing, write nothing and never see a store, a ledger, a
clock or an id generator; the service keeps every guard, read, port call, transaction and order.
"""

from __future__ import annotations

from thoth.application.services.memory_safety import REDACTED_MEMORY, memory_text_is_unsafe
from thoth.application.services.memory_shape import memory_body
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.memory import (
    FullMemoryRevision,
    MemoryProjection,
    MemoryRecord,
    MemoryReviewVerdict,
    MemoryRoleReview,
    MemoryTransition,
    MemoryTransitionReceipt,
)
from thoth.domain.memory_preparation import FullMemoryPromotionResult
from thoth.domain.memory_relation import MemoryRelationJudgment

_UNKNOWN_SHAPE_CHARS = 640


def candidate_set_digest(records: tuple[MemoryRecord, ...]) -> str:
    return domain_digest(
        "MEMORY_CANDIDATE_SET",
        "1.0.0",
        canonical_payload(
            {"records": tuple(sorted((item.memory_id, item.revision_digest) for item in records))}
        ),
    )


def memory_words_text(
    candidate: MemoryRecord, content_excerpt: str, body: tuple[str, str] | None
) -> str:
    if candidate.assertion is not None:
        words_text = candidate.assertion
    else:
        words_text = content_excerpt if body is None else body[1]
    return words_text


def candidate_is_unsafe(candidate: MemoryRecord, content_excerpt: str) -> bool:
    unsafe = memory_text_is_unsafe(content_excerpt) or candidate.assertion == REDACTED_MEMORY
    return unsafe


def promotion_result(
    revisions: list[FullMemoryRevision],
    receipts: list[MemoryTransitionReceipt],
    projections: tuple[MemoryProjection, ...],
    judgments: list[MemoryRelationJudgment],
) -> FullMemoryPromotionResult:
    """Project reviewed revisions into the public promotion result."""
    checkpoint = (
        projections[0].rebuild_checkpoint
        if projections
        else domain_digest(
            "MEMORY_PROJECTION_CHECKPOINT",
            "1.0.0",
            canonical_payload({"source_revision_digests": ()}),
        )
    )
    return FullMemoryPromotionResult(
        committed=tuple(item for item in revisions if item.transition == MemoryTransition.COMMIT),
        revised=tuple(item for item in revisions if item.transition == MemoryTransition.REVISE),
        held=tuple(item for item in revisions if item.transition == MemoryTransition.HOLD),
        quarantined=tuple(
            item for item in revisions if item.transition == MemoryTransition.QUARANTINE
        ),
        receipts=tuple(receipts),
        projection_checkpoint=checkpoint,
        projection_state="BUILT" if projections else "DEFERRED_SCOPE",
        relation_judgments=tuple(judgments),
    )


def build_content_excerpt(source_ref: str | None, assertion: str | None, snapshot: object) -> str:
    """The stored text: the source, then one short line (a lesson's words, or the record's)."""

    parts = [source_ref or "", assertion or ""]
    if assertion is None and snapshot is not None:
        content = getattr(snapshot, "content", {})
        body = memory_body(content)
        if body is not None:
            parts.append(body[0])
        else:
            # An unfamiliar record: keep the start of it, within one recalled item's share.
            parts.append(canonical_payload(content).decode()[:_UNKNOWN_SHAPE_CHARS])
    return "\n".join(part for part in parts if part)[:8_000]


def reduce_reviews(reviews: tuple[MemoryRoleReview, ...]) -> MemoryTransition:
    verdicts = {review.verdict for review in reviews}
    if MemoryReviewVerdict.QUARANTINE in verdicts:
        return MemoryTransition.QUARANTINE
    if MemoryReviewVerdict.HOLD in verdicts:
        return MemoryTransition.HOLD
    if MemoryReviewVerdict.REVISE in verdicts:
        return MemoryTransition.REVISE
    return MemoryTransition.COMMIT
