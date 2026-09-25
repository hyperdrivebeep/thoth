from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, model_validator

from thoth.domain.actor import ActorRef
from thoth.domain.base import DomainModel
from thoth.domain.enums import EntityType, ReceiptClaimScope, ReceiptType
from thoth.domain.ids import ProjectId, RevisionId, Sha256


class EntitySnapshot(DomainModel):
    snapshot_id: str
    project_id: ProjectId
    entity_type: EntityType
    entity_id: str
    schema_version: str
    content: dict[str, object]
    content_digest: Sha256


class SemanticRevision(DomainModel):
    revision_id: RevisionId
    project_id: ProjectId
    entity_type: EntityType
    entity_id: str
    snapshot_id: str
    parent_revision_digests: tuple[Sha256, ...]
    actor: ActorRef
    reason: str
    evidence_refs: tuple[str, ...]
    affected_refs: tuple[str, ...]
    revision_digest: Sha256
    created_at: AwareDatetime
    schema_version: str = "1.0.0"


class ImpactPropagationPlan(DomainModel):
    stale_refs: tuple[str, ...] = ()
    invalidated_refs: tuple[str, ...] = ()
    recalculate_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def require_disjoint_sets(self) -> ImpactPropagationPlan:
        groups = [set(self.stale_refs), set(self.invalidated_refs), set(self.recalculate_refs)]
        if any(groups[left] & groups[right] for left in range(3) for right in range(left + 1, 3)):
            raise ValueError("impact propagation sets must be disjoint")
        return self


class StagedRevision(DomainModel):
    snapshot: EntitySnapshot
    revision: SemanticRevision


class RevisionChangeSet(DomainModel):
    changeset_id: str
    project_id: ProjectId
    expected_heads: dict[str, Sha256]
    expected_head_set_digest: Sha256 | None = None
    staged_revisions: tuple[StagedRevision, ...]
    impact_plan: ImpactPropagationPlan
    actor: ActorRef
    reason: str
    receipt_type: ReceiptType = ReceiptType.TRANSITION
    receipt_claim_scopes: tuple[ReceiptClaimScope, ...] = (
        ReceiptClaimScope.TRANSITION_RECORDED,
        ReceiptClaimScope.ARTIFACT_INTEGRITY,
        ReceiptClaimScope.PROVENANCE_BOUND,
    )


class SemanticDiffEntry(DomainModel):
    path: str
    before_present: bool
    after_present: bool
    before: object | None = None
    after: object | None = None


class RevisionComparison(DomainModel):
    project_id: ProjectId
    entity_type: EntityType
    entity_id: str
    left_revision_id: RevisionId
    right_revision_id: RevisionId
    changes: tuple[SemanticDiffEntry, ...]


class SemanticMergeState(StrEnum):
    AUTO_MERGED = "AUTO_MERGED"
    OPEN_CONFLICT = "OPEN_CONFLICT"


class SemanticMergeGates(DomainModel):
    common_ancestor: bool
    schema_compatible: bool
    authority: bool
    cutoff: bool
    policy: bool
    dependency: bool
    domain: bool
    current_head: bool


class SemanticMergeResult(DomainModel):
    merge_id: str
    project_id: ProjectId
    entity_type: EntityType
    entity_id: str
    state: SemanticMergeState
    common_ancestor_digest: Sha256 | None = None
    left_revision_digest: Sha256
    right_revision_digest: Sha256
    left_changed_paths: tuple[str, ...]
    right_changed_paths: tuple[str, ...]
    conflict_paths: tuple[str, ...]
    reason_codes: tuple[str, ...]
    gates: SemanticMergeGates
    merged_revision_digest: Sha256 | None = None
    receipt_ref: str | None = None
    head_mutated: bool
    last_write_wins: Literal[False] = False
    semantic_truth_certified: Literal[False] = False


class SemanticMergeReceipt(DomainModel):
    receipt_id: str
    project_id: ProjectId
    merge_id: str
    state: SemanticMergeState
    parent_revision_digests: tuple[Sha256, Sha256]
    common_ancestor_digest: Sha256 | None = None
    conflict_paths: tuple[str, ...]
    gates: SemanticMergeGates
    integrity: Literal["VALID"] = "VALID"
    provenance: Literal["CANONICAL_REVISION_DAG"] = "CANONICAL_REVISION_DAG"
    authorization: Literal["SEPARATE"] = "SEPARATE"
    semantic_truth: Literal["NOT_CERTIFIED"] = "NOT_CERTIFIED"
    receipt_digest: Sha256
    recorded_at: AwareDatetime
