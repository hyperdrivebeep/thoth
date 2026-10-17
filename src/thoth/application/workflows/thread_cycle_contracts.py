from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pydantic import model_validator

from thoth.application.reducers import SufficiencySignals
from thoth.application.services.full_project_memory import FullMemoryPromotionResult
from thoth.application.services.revision_service import CommitDisposition, CommitResult
from thoth.domain.action import ActionCompilationPolicy, ActionPlan, ActionPlanCompilationResult
from thoth.domain.actor import ActorRef
from thoth.domain.base import DomainModel
from thoth.domain.criterion import CriterionCandidate
from thoth.domain.evidence import EvidenceSpan, InformationSufficiencyAssessment
from thoth.domain.hypothesis import HypothesisPortfolio
from thoth.domain.outcome import OutcomeRecord


@dataclass(frozen=True)
class ThreadCycleCommand:
    case_id: str
    project_id: str
    thread_id: str
    object_id: str
    problem: str
    cutoff_at: datetime
    criteria: tuple[CriterionCandidate, ...]
    evidence: tuple[EvidenceSpan, ...]
    sufficiency_signals: SufficiencySignals
    action_policy: ActionCompilationPolicy
    actor: ActorRef
    policy_version: str
    model_policy_ref: str
    expected_head_overrides: dict[str, str] = field(default_factory=dict)
    memory_scope: dict[str, str] = field(default_factory=dict)


class ThreadCycleResult(DomainModel):
    assessment: InformationSufficiencyAssessment
    portfolio: HypothesisPortfolio
    action_plan: ActionPlan
    action_compilation: ActionPlanCompilationResult
    outcome: OutcomeRecord | None = None
    commit: CommitResult
    memory_ids: tuple[str, ...]
    model_ids: tuple[str, ...]
    full_memory: FullMemoryPromotionResult | None = None
    semantic_repair_attempted: bool = False


class ThreadCycleBranchResult(DomainModel):
    project_id: str
    thread_id: str
    terminal_state: Literal["BRANCHED"] = "BRANCHED"
    reason_code: Literal["THREAD_CYCLE_HEAD_CONFLICT"] = "THREAD_CYCLE_HEAD_CONFLICT"
    commit: CommitResult
    downstream_after_branch_executed: Literal[False] = False
    prior_acquisition_completed: bool = False
    input_disposition: Literal["RETAINED_FOR_EXPLICIT_RETRY"] = "RETAINED_FOR_EXPLICIT_RETRY"

    @model_validator(mode="after")
    def require_branch_commit(self) -> ThreadCycleBranchResult:
        if self.commit.disposition != CommitDisposition.BRANCH:
            raise ValueError("branch result requires a branch commit")
        return self
