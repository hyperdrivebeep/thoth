from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, Field

from thoth.domain.base import DomainModel
from thoth.domain.ids import ProjectId, Sha256


class BaselineScope(StrEnum):
    MISSION_PRODUCT = "MISSION_PRODUCT"
    RESEARCH_HYPOTHESIS = "RESEARCH_HYPOTHESIS"
    CODE_INFRASTRUCTURE = "CODE_INFRASTRUCTURE"
    EVALUATION_EVIDENCE = "EVALUATION_EVIDENCE"
    SAFETY_COMPLIANCE = "SAFETY_COMPLIANCE"
    RELEASE_OPERATIONS = "RELEASE_OPERATIONS"


class ProjectHeadSet(DomainModel):
    project_id: ProjectId
    head_map: dict[str, Sha256]
    head_set_digest: Sha256
    revision: int = Field(ge=1)
    generated_at: AwareDatetime


class BaselineCandidate(DomainModel):
    candidate_id: str
    project_id: ProjectId
    thread_id: str | None = None
    scope: BaselineScope
    purpose: str
    head_map: dict[str, Sha256]
    project_head_set_digest: Sha256
    policy_version: str
    evidence_refs: tuple[str, ...] = ()
    state: Literal[
        "PENDING_PROTECTED_DECISION", "APPROVED", "REJECTED", "STALE"
    ] = "PENDING_PROTECTED_DECISION"
    protected: Literal[True] = True
    candidate_digest: Sha256
    created_at: AwareDatetime


class BaselineSet(DomainModel):
    baseline_set_id: str
    project_id: ProjectId
    scope: BaselineScope
    purpose: str
    head_map: dict[str, Sha256]
    source_candidate_id: str
    policy_version: str
    lifecycle: Literal["CURRENT", "SUPERSEDED", "STALE"] = "CURRENT"
    supersedes_baseline_set_id: str | None = None
    authority_actor_ref: str
    authority_role_assignment_ref: str
    recalculation_required: bool = False
    baseline_set_digest: Sha256
    created_at: AwareDatetime


class BaselineDecision(DomainModel):
    decision_id: str
    project_id: ProjectId
    candidate_id: str
    decision: Literal["APPROVE", "REJECT"]
    actor_ref: str
    role_assignment_ref: str
    approved_digest: Sha256
    resulting_baseline_set_id: str | None = None
    protected: Literal[True] = True
    decision_digest: Sha256
    decided_at: AwareDatetime


class MultiBaselineProjection(DomainModel):
    project_head_set: ProjectHeadSet
    baseline_candidates: tuple[BaselineCandidate, ...]
    current_baseline_sets: tuple[BaselineSet, ...]
    stale_baseline_set_ids: tuple[str, ...] = ()
