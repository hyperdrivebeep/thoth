from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.enums import (
    ActionCompilationStatus,
    ActionPlanCompilationStatus,
    ActionState,
    ExecutionAuthority,
    Reversibility,
    RiskTier,
)
from thoth.domain.ids import ActionId, DecisionObjectId, HypothesisId, Sha256
from thoth.domain.r2_loop import R2ExecutionSummary
from thoth.domain.recovery import RecoveryCandidateRevision

ActionPurpose = Literal[
    "INFORMATION_ACQUISITION",
    "HYPOTHESIS_DISCRIMINATION",
    "ANALYSIS_COMPUTATION",
    "SIMULATION",
    "EXPERIMENT_TEST",
    "STATE_OR_DESIGN_CHANGE",
    "RISK_REDUCTION",
    "REQUIREMENT_VERIFICATION",
    "COMMUNICATION_SUBMISSION",
    "GOVERNANCE_ESCALATION",
    "RESTORE_COMPENSATE",
]


class ActionRiskFacts(DomainModel):
    changes_local_draft: bool = False
    runs_untrusted_code: bool = False
    external_write: bool = False
    physical_action: bool = False
    changes_official_baseline: bool = False
    changes_official_kpi: bool = False
    grants_waiver: bool = False
    changes_safety_threshold: bool = False
    finalizes_model_weights: bool = False


class ActionDraft(DomainModel):
    action_id: ActionId
    object_id: DecisionObjectId
    hypothesis_ids: tuple[HypothesisId, ...]
    action_family: str
    specification: str
    expected_information_value: str
    estimated_cost: Decimal | None = Field(default=None, ge=0)
    estimated_seconds: int | None = Field(default=None, ge=1)
    reversibility: Reversibility
    effect_facts: ActionRiskFacts
    effect_completeness_confirmed: bool = Field(
        default=False,
        description=(
            "True only when every listed real-world effect has been explicitly assessed; "
            "False causes fail-closed protected routing."
        ),
    )
    source_refs: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    primary_purpose: ActionPurpose | None = None


class ActionCompilationPolicy(DomainModel):
    minimum_tier_by_family: dict[str, RiskTier]
    approver_role_by_family: dict[str, str] = Field(default_factory=dict)
    unknown_family_tier: RiskTier = RiskTier.R3


class ActionCandidate(DomainModel):
    action_id: ActionId
    object_id: DecisionObjectId
    hypothesis_ids: tuple[HypothesisId, ...]
    action_family: str
    specification: str
    expected_information_value: str
    estimated_cost: Decimal | None = Field(default=None, ge=0)
    estimated_seconds: int | None = Field(default=None, ge=1)
    risk_tier: RiskTier
    execution_authority: ExecutionAuthority
    reversibility: Reversibility
    external_write: bool
    sandbox_required: bool
    state: ActionState = ActionState.PROPOSED
    required_approver_role: str | None = None
    source_refs: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    primary_purpose: ActionPurpose | None = None
    effect_facts: ActionRiskFacts | None = None
    effect_completeness_confirmed: bool = False
    schema_version: str = "1.0.0"

    @model_validator(mode="after")
    def enforce_authority(self) -> ActionCandidate:
        if not self.source_refs and not self.missing_evidence:
            raise ValueError("action requires source references or an explicit evidence gap")
        expected_tier = {
            ExecutionAuthority.AUTO_R0: RiskTier.R0,
            ExecutionAuthority.PREAUTHORIZED_R1: RiskTier.R1,
            ExecutionAuthority.SANDBOX_ONLY_R2: RiskTier.R2,
            ExecutionAuthority.HUMAN_REQUIRED_R3: RiskTier.R3,
            ExecutionAuthority.PROHIBITED_R4: RiskTier.R4,
        }[self.execution_authority]
        if self.risk_tier != expected_tier:
            raise ValueError("risk tier does not match execution authority")
        if self.risk_tier in {RiskTier.R0, RiskTier.R1, RiskTier.R2} and self.external_write:
            raise ValueError("R0-R2 action cannot write externally")
        if self.risk_tier == RiskTier.R2 and not self.sandbox_required:
            raise ValueError("R2 action requires sandbox")
        if self.risk_tier == RiskTier.R3 and self.required_approver_role is None:
            raise ValueError("R3 action requires approver role")
        if self.risk_tier == RiskTier.R4 and self.state != ActionState.PROHIBITED:
            raise ValueError("R4 action must be prohibited")
        return self


class ActionCompilationResult(DomainModel):
    status: ActionCompilationStatus
    candidate: ActionCandidate | None = None
    reason: str
    invalid_source_refs: tuple[str, ...] = ()
    invalid_hypothesis_refs: tuple[str, ...] = ()


class DecisionCriterion(DomainModel):
    criterion_id: str
    name: str
    mandatory: bool
    rationale: str


class ActionEvaluation(DomainModel):
    action_id: ActionId
    criterion_id: str
    scenario: str
    assessment: str
    evidence_refs: tuple[str, ...]


class DecisionAnalysis(DomainModel):
    decision: str
    criteria: tuple[DecisionCriterion, ...]
    evaluations: tuple[ActionEvaluation, ...]
    uncertainty: str
    sensitivity: str
    preference_question: str | None = None


class ActionPlanDraft(DomainModel):
    plan_id: str
    object_id: DecisionObjectId
    alternatives: tuple[ActionDraft, ...]
    decision_analysis: DecisionAnalysis
    proposed_frontier: tuple[ActionId, ...]
    plan_revision_digest: Sha256


class ActionPlan(DomainModel):
    plan_id: str
    object_id: DecisionObjectId
    alternatives: tuple[ActionCandidate, ...]
    decision_analysis: DecisionAnalysis
    frontier: tuple[ActionId, ...]
    plan_revision_digest: Sha256
    next_action_order: tuple[ActionId, ...] = ()
    last_r2_execution: R2ExecutionSummary | None = None
    recovery_candidate: RecoveryCandidateRevision | None = None
    schema_version: str = "1.0.0"

    @model_validator(mode="after")
    def frontier_must_reference_alternatives(self) -> ActionPlan:
        action_ids = {item.action_id for item in self.alternatives}
        if len(self.alternatives) < 2:
            raise ValueError("action plan requires at least two alternatives")
        if len({item.action_family for item in self.alternatives}) < 2:
            raise ValueError("action plan requires at least two action families")
        if not set(self.frontier).issubset(action_ids):
            raise ValueError("frontier references unknown action")
        return self


class ActionPlanCompilationResult(DomainModel):
    status: ActionPlanCompilationStatus
    plan: ActionPlan | None = None
    action_results: tuple[ActionCompilationResult, ...]
    hold_reasons: tuple[str, ...] = ()
    adjustments: tuple[str, ...] = ()


class ProtectedActionCard(DomainModel):
    action_id: ActionId
    plan_revision_digest: Sha256
    step_id: str
    exact_input_digests: tuple[Sha256, ...]
    target_revision: Sha256
    baseline_revision: Sha256 | None = None
    tool: str
    environment: str
    egress: str
    budget: str
    time_limit_seconds: int = Field(ge=1)
    stop_conditions: tuple[str, ...]
    compensation: str
    required_roles: tuple[str, ...]
    expires_at: AwareDatetime
    single_use: bool = True
    schema_version: str = "1.0.0"
