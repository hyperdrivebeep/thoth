"""Post-effect learning is a separate, revision-bound checkpoint."""

from typing import Literal

from thoth.domain.base import DomainModel
from thoth.domain.memory_preparation import FullMemoryPromotionResult
from thoth.domain.research_request import RevisionRef
from thoth.domain.test_validity import TestValidityAssessment


class CommittedExecutionBasis(DomainModel):
    execution_attempt_ref: str
    outcome_revision_ref: RevisionRef
    updated_revision_refs: tuple[RevisionRef, ...]
    assessments: tuple[TestValidityAssessment, ...]
    observation_refs: tuple[str, ...]


class PostExecutionLearningBasis(DomainModel):
    project_id: str
    thread_id: str
    request_ref: RevisionRef
    execution: CommittedExecutionBasis
    policy_digest: str
    cutoff_at: str
    source_basis_digest: str
    source_refs: tuple[str, ...]


class PostExecutionLearningResult(DomainModel):
    record_kind: Literal["PostExecutionLearningResult"] = "PostExecutionLearningResult"
    schema_version: Literal["2.1.0"] = "2.1.0"
    basis: PostExecutionLearningBasis
    basis_digest: str
    state: Literal["PENDING", "COMMITTED", "HELD", "STALE"]
    memory_revision_refs: tuple[str, ...] = ()
    review_receipt_refs: tuple[str, ...] = ()
    reason_code: str | None = None
    promotion: FullMemoryPromotionResult | None = None
