"""The stored form of one reviewed memory candidate: a version and its transition receipt."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import AwareDatetime

from thoth.application.services.memory_safety import REDACTED_MEMORY
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import MemoryKind
from thoth.domain.memory import (
    FullMemoryRevision,
    MemoryRecord,
    MemoryRoleReview,
    MemoryTransition,
    MemoryTransitionReceipt,
)
from thoth.domain.revision import SemanticRevision


def support_status(*, owner_valid: bool, contradiction: bool, ambiguous: bool) -> str:
    """SUPPORTED, AMBIGUOUS (relation unclear) or CONFLICTING (another value for the record)."""

    if owner_valid and not (contradiction or ambiguous):
        return "SUPPORTED"
    return "AMBIGUOUS" if ambiguous and not contradiction else "CONFLICTING"


def seal_revision(
    *,
    revision_id: str,
    candidate: MemoryRecord,
    thread_id: str,
    owner: SemanticRevision | None,
    content_excerpt: str,
    query_terms: tuple[str, ...],
    unsafe: bool,
    owner_valid: bool,
    support: str,
    reviews: Sequence[MemoryRoleReview],
    transition: MemoryTransition,
    cutoff_at: AwareDatetime,
    scope: Mapping[str, str],
    parent_revision_digest: str | None,
    created_at: AwareDatetime,
) -> FullMemoryRevision:
    recall_eligible = transition == MemoryTransition.COMMIT
    draft: dict[str, object] = {
        "memory_revision_id": revision_id,
        "memory_id": candidate.memory_id,
        "project_id": candidate.project_id,
        "origin_thread_id": thread_id,
        "payload_mode": candidate.payload_mode.value,
        "kind": candidate.kind.value,
        "owner_revision_ref": candidate.owner_revision_ref,
        "source_ref": candidate.source_ref,
        "assertion": REDACTED_MEMORY if unsafe else candidate.assertion,
        "content_excerpt": REDACTED_MEMORY if unsafe else content_excerpt,
        "scope": dict(scope),
        "evidence_refs": () if owner is None else owner.evidence_refs,
        "query_terms": () if unsafe else query_terms,
        "support_status": support,
        "authority_status": "AUTHORITATIVE" if owner_valid else "UNKNOWN",
        "cutoff_at": cutoff_at,
        "cutoff_valid": owner_valid,
        "reviews": tuple(review.model_dump(mode="json") for review in reviews),
        "transition": transition.value,
        "recall_eligible": recall_eligible,
        "action_eligible": recall_eligible and candidate.kind == MemoryKind.FACT,
        "canonical_truth": True,
        "semantic_truth_certified": False,
        "parent_revision_digest": parent_revision_digest,
        "created_at": created_at,
        "schema_version": "2.0.0",
    }
    digest = domain_digest("FULL_MEMORY_REVISION", "2.0.0", canonical_payload(draft))
    return FullMemoryRevision.model_validate({**draft, "revision_digest": digest})


def seal_receipt(
    *,
    receipt_id: str,
    revision: FullMemoryRevision,
    reviews: Sequence[MemoryRoleReview],
    recorded_at: AwareDatetime,
) -> MemoryTransitionReceipt:
    draft: dict[str, object] = {
        "receipt_id": receipt_id,
        "project_id": revision.project_id,
        "memory_revision_id": revision.memory_revision_id,
        "transition": revision.transition.value,
        "source_revision_ref": revision.owner_revision_ref,
        "review_basis_digests": tuple(review.basis_digest for review in reviews),
        "integrity": "VALID",
        "provenance": "OWNER_REVISION_BOUND",
        "authorization": "PROJECT_SCOPED",
        "semantic_truth": "NOT_CERTIFIED",
        "recorded_at": recorded_at,
    }
    return MemoryTransitionReceipt.model_validate(
        {
            **draft,
            "receipt_digest": domain_digest(
                "MEMORY_TRANSITION_RECEIPT", "1.0.0", canonical_payload(draft)
            ),
        }
    )
