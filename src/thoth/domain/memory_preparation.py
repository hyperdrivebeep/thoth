"""Typed, non-canonical plans for review outside a write transaction."""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime

from thoth.domain.actor import ActorRef
from thoth.domain.auth import AuthenticatedActorContext
from thoth.domain.base import DomainModel
from thoth.domain.memory import (
    FullMemoryRevision,
    MemoryProjection,
    MemoryRecord,
    MemoryTransitionReceipt,
)


class MemoryAuthorityBasis(DomainModel):
    project_digest: str
    policy_id: str
    policy_revision: int
    policy_digest: str
    policy_record_digest: str
    actor_context: AuthenticatedActorContext | None = None
    role_digest: str | None = None
    session_digest: str | None = None


class MemoryPreparationBasis(DomainModel):
    project_id: str
    cutoff_at: AwareDatetime
    scope: tuple[tuple[str, str], ...]
    actor: ActorRef | None
    head_set_digest: str
    memory_revision_set: tuple[str, ...]
    candidate_set_digest: str
    authority: MemoryAuthorityBasis | None = None


class FullMemoryPromotionResult(DomainModel):
    committed: tuple[FullMemoryRevision, ...]
    revised: tuple[FullMemoryRevision, ...]
    held: tuple[FullMemoryRevision, ...]
    quarantined: tuple[FullMemoryRevision, ...]
    receipts: tuple[MemoryTransitionReceipt, ...]
    projection_checkpoint: str
    projection_state: Literal["BUILT", "DEFERRED_SCOPE"] = "BUILT"


class PreparedMemoryPromotion(DomainModel):
    basis: MemoryPreparationBasis
    candidates: tuple[MemoryRecord, ...]
    revisions: tuple[FullMemoryRevision, ...]
    receipts: tuple[MemoryTransitionReceipt, ...]
    projections: tuple[MemoryProjection, ...]
    result: FullMemoryPromotionResult


class MemoryPreparationHeadChanged(ValueError):
    """A concurrent semantic change must use the existing branch contract."""
