from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.enums import (
    MemoryKind,
    MemoryLifecycle,
    MemoryPayloadMode,
    RecallEligibility,
)
from thoth.domain.ids import ProjectId, Sha256


class MemoryRecord(DomainModel):
    memory_id: str
    project_id: ProjectId
    payload_mode: MemoryPayloadMode
    kind: MemoryKind
    owner_revision_ref: str
    source_ref: str | None = None
    assertion: str | None = None
    recall_eligibility: RecallEligibility
    lifecycle: MemoryLifecycle = MemoryLifecycle.CURRENT
    revision_digest: Sha256
    schema_version: str = "1.0.0"

    @model_validator(mode="after")
    def enforce_payload_boundary(self) -> MemoryRecord:
        if self.payload_mode == MemoryPayloadMode.DOMAIN_REFERENCE:
            if self.kind == MemoryKind.LESSON or self.source_ref is None:
                raise ValueError("domain reference memory requires a non-lesson source reference")
            if self.assertion is not None:
                raise ValueError("domain reference memory cannot contain a free assertion")
        else:
            if self.kind != MemoryKind.LESSON or not self.assertion:
                raise ValueError("memory assertion mode is limited to an explicit lesson")
        return self


class RecallContext(DomainModel):
    project_id: ProjectId
    included_records: tuple[MemoryRecord, ...]
    included_memory_ids: tuple[str, ...]
    included_revision_refs: tuple[str, ...]
    excluded_reason_counts: dict[str, int]
    approximate_characters: int = Field(ge=0)


class MemoryReviewRole(StrEnum):
    FACTS = "FACTS"
    REFLECTION = "REFLECTION"
    DREAM = "DREAM"
    TEAM = "TEAM"


class MemoryReviewVerdict(StrEnum):
    PASS = "PASS"
    REVISE = "REVISE"
    HOLD = "HOLD"
    QUARANTINE = "QUARANTINE"


class MemoryTransition(StrEnum):
    COMMIT = "COMMIT"
    REVISE = "REVISE"
    HOLD = "HOLD"
    QUARANTINE = "QUARANTINE"


class MemoryRoleReview(DomainModel):
    role: MemoryReviewRole
    verdict: MemoryReviewVerdict
    reason_code: str
    basis_digest: Sha256
    independently_computed: Literal[True] = True
    institutionally_independent: Literal[False] = False
    model_id: str | None = None
    prompt_version: str | None = None
    model_input_digest: Sha256 | None = None
    model_output_digest: Sha256 | None = None
    schema_digest: Sha256 | None = None
    scripted: bool | None = None


class MemoryReviewModelOutput(DomainModel):
    verdict: MemoryReviewVerdict
    reason_code: str = Field(min_length=3, max_length=120, pattern=r"^[A-Z0-9_]+$")


class MemoryReviewContext(DomainModel):
    role: MemoryReviewRole
    candidate_digest: Sha256
    fields: dict[str, object]
    context_digest: Sha256
    bounded: Literal[True] = True


class FullMemoryRevision(DomainModel):
    memory_revision_id: str
    memory_id: str
    project_id: ProjectId
    origin_thread_id: str
    payload_mode: MemoryPayloadMode
    kind: MemoryKind
    owner_revision_ref: Sha256
    source_ref: str | None = None
    assertion: str | None = None
    content_excerpt: str
    scope: dict[str, str]
    evidence_refs: tuple[str, ...]
    query_terms: tuple[str, ...]
    support_status: str
    authority_status: str
    cutoff_at: AwareDatetime
    cutoff_valid: bool
    reviews: tuple[MemoryRoleReview, ...] = Field(min_length=4, max_length=4)
    transition: MemoryTransition
    recall_eligible: bool
    action_eligible: bool
    canonical_truth: Literal[True] = True
    semantic_truth_certified: Literal[False] = False
    parent_revision_digest: Sha256 | None = None
    revision_digest: Sha256
    created_at: AwareDatetime
    schema_version: str = "2.0.0"

    @model_validator(mode="after")
    def enforce_transition_gates(self) -> FullMemoryRevision:
        roles = {review.role for review in self.reviews}
        if roles != set(MemoryReviewRole):
            raise ValueError("full memory requires one independent review from every role")
        if self.transition != MemoryTransition.COMMIT and (
            self.recall_eligible or self.action_eligible
        ):
            raise ValueError("non-committed memory cannot be recall or action eligible")
        if self.action_eligible and not self.recall_eligible:
            raise ValueError("action eligibility requires recall eligibility")
        return self


class MemoryTransitionReceipt(DomainModel):
    receipt_id: str
    project_id: ProjectId
    memory_revision_id: str
    transition: MemoryTransition
    source_revision_ref: Sha256
    review_basis_digests: tuple[Sha256, ...] = Field(min_length=4, max_length=4)
    integrity: Literal["VALID"] = "VALID"
    provenance: Literal["OWNER_REVISION_BOUND"] = "OWNER_REVISION_BOUND"
    authorization: Literal["PROJECT_SCOPED"] = "PROJECT_SCOPED"
    semantic_truth: Literal["NOT_CERTIFIED"] = "NOT_CERTIFIED"
    receipt_digest: Sha256
    recorded_at: AwareDatetime


class FullMemoryContextPack(DomainModel):
    context_pack_id: str
    project_id: ProjectId
    thread_id: str
    query: str
    target_use: str
    cutoff_at: AwareDatetime
    included: tuple[FullMemoryRevision, ...]
    excluded_reason_counts: dict[str, int]
    query_digest: Sha256
    injected_into_thread: bool
    canonical_truth: Literal[False] = False
    created_at: AwareDatetime


class MemoryProjection(DomainModel):
    projection_id: str
    project_id: ProjectId
    projection_type: Literal["KEYWORD", "VECTOR", "GRAPH", "SUMMARY"]
    source_revision_digests: tuple[Sha256, ...]
    payload_digest: Sha256
    rebuild_checkpoint: Sha256
    canonical_truth: Literal[False] = False
    created_at: AwareDatetime
