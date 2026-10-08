"""Action RPC input models; validation and defaults retained from actions_full."""

from __future__ import annotations

from pydantic import Field, JsonValue

from thoth.application.commands.action_effort import HumanEffortEstimateInput
from thoth.domain.base import DomainModel


class ProjectInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)


class ActionListInput(ProjectInput):
    object_id: str | None = Field(default=None, max_length=160)
    portfolio_id: str | None = Field(default=None, max_length=160)
    purpose: str | None = Field(default=None, max_length=80)
    proposal_state: str | None = Field(default=None, max_length=80)
    policy_state: str | None = Field(default=None, max_length=80)
    authorization_state: str | None = Field(default=None, max_length=80)


class ActionReadInput(ProjectInput):
    action_id: str = Field(min_length=1, max_length=160)
    revision_digest: str | None = Field(default=None, min_length=64, max_length=64)


class PortfolioListInput(ProjectInput):
    object_id: str | None = Field(default=None, max_length=160)
    decision_state: str | None = Field(default=None, max_length=80)


class PortfolioReadInput(ProjectInput):
    portfolio_id: str = Field(min_length=1, max_length=160)
    revision_digest: str | None = Field(default=None, min_length=64, max_length=64)


class PlanReadInput(ProjectInput):
    plan_id: str = Field(min_length=1, max_length=160)
    revision_digest: str | None = Field(default=None, min_length=64, max_length=64)


class StepReadInput(PlanReadInput):
    step_id: str = Field(min_length=1, max_length=160)


class ImpactReadInput(ProjectInput):
    action_id: str | None = Field(default=None, max_length=160)
    plan_id: str | None = Field(default=None, max_length=160)
    step_id: str | None = Field(default=None, max_length=160)
    revision_digest: str | None = Field(default=None, min_length=64, max_length=64)


class AuthorizationReadInput(ProjectInput):
    authorization_id: str = Field(min_length=1, max_length=160)


class AuditReadInput(ProjectInput):
    action_id: str | None = Field(default=None, max_length=160)
    plan_id: str | None = Field(default=None, max_length=160)
    revision_digest: str | None = Field(default=None, min_length=64, max_length=64)


class ActionRevisionBound(ActionReadInput):
    expected_revision_digest: str = Field(min_length=64, max_length=64)


class ReviseInput(ActionRevisionBound):
    patch: dict[str, JsonValue] = Field(default_factory=dict)
    evidence_refs: tuple[str, ...]
    reason: str = Field(min_length=1, max_length=5_000)
    human_effort_estimates: tuple[HumanEffortEstimateInput, ...] = ()
    estimator_ref: str | None = Field(default=None, min_length=1, max_length=160)


class PortfolioComposeInput(ProjectInput):
    object_id: str = Field(min_length=1, max_length=160)
    action_ids: tuple[str, ...] = Field(min_length=2)
    decision_need: str = Field(min_length=1, max_length=5_000)
    criteria_proposal: tuple[dict[str, JsonValue], ...] = ()
    expected_object_revision: str | None = Field(default=None, min_length=64, max_length=64)
    portfolio_id: str | None = Field(default=None, max_length=160)


class PortfolioEvaluateInput(PortfolioReadInput):
    evaluation_method: str = Field(min_length=1, max_length=160)
    policy_criteria_ref: str = Field(min_length=1, max_length=260)
    preference_inputs: tuple[dict[str, JsonValue], ...] = ()


class RecommendInput(PortfolioReadInput):
    scenario_ref: str | None = Field(default=None, max_length=260)
    rationale: str = Field(min_length=1, max_length=5_000)


class SelectInput(ProjectInput):
    portfolio_id: str = Field(min_length=1, max_length=160)
    action_id: str = Field(min_length=1, max_length=160)
    decision_context: dict[str, JsonValue]
    actor_or_agent_ref: str = Field(min_length=1, max_length=160)
    expected_portfolio_revision: str = Field(min_length=64, max_length=64)


class PlanComposeInput(ProjectInput):
    object_id: str = Field(min_length=1, max_length=160)
    selected_action_refs: tuple[str, ...]
    step_candidates: tuple[dict[str, JsonValue], ...]
    dependency_edges: tuple[dict[str, str], ...]
    expected_object_revision: str | None = Field(default=None, min_length=64, max_length=64)
    plan_id: str | None = Field(default=None, max_length=160)


class PlanRevisionBound(PlanReadInput):
    expected_revision_digest: str = Field(min_length=64, max_length=64)


class PlanReviseInput(PlanRevisionBound):
    patch: dict[str, JsonValue]
    evidence_refs: tuple[str, ...]
    reason: str = Field(min_length=1, max_length=5_000)


class PlanRevalidateInput(PlanReadInput):
    trigger_reason: str = Field(min_length=1, max_length=2_000)


class StepAddInput(ProjectInput):
    plan_id: str = Field(min_length=1, max_length=160)
    step_spec: dict[str, JsonValue]
    dependency_refs: tuple[str, ...]
    expected_plan_revision: str = Field(min_length=64, max_length=64)


class StepReviseInput(ProjectInput):
    plan_id: str = Field(min_length=1, max_length=160)
    step_id: str = Field(min_length=1, max_length=160)
    patch: dict[str, JsonValue]
    evidence_refs: tuple[str, ...]
    reason: str = Field(min_length=1, max_length=5_000)
    expected_plan_revision: str = Field(min_length=64, max_length=64)


class ImpactRecalculateInput(PlanReadInput):
    trigger_refs: tuple[str, ...]


class PolicyClassifyInput(PlanReadInput):
    policy_version: str | None = Field(default=None, max_length=160)


class AuthorizationPrepareInput(ProjectInput):
    plan_id: str = Field(min_length=1, max_length=160)
    step_id: str = Field(min_length=1, max_length=160)
    plan_revision_digest: str = Field(min_length=64, max_length=64)
    predecessor_output_digests: tuple[str, ...]
    target_baseline_digests: tuple[str, ...]
    policy_version: str = Field(min_length=1, max_length=160)


class AuthorizationDecideInput(ProjectInput):
    authorization_id: str = Field(min_length=1, max_length=160)
    decision: str = Field(pattern=r"^(APPROVE|REJECT)$")
    actor_ref: str = Field(min_length=1, max_length=160)
    role_assignment_ref: str = Field(min_length=1, max_length=160)
    approved_digest: str = Field(min_length=64, max_length=64)
    reason: str | None = Field(default=None, max_length=2_000)
    dissent: str | None = Field(default=None, max_length=2_000)


class CompensationCreateInput(ProjectInput):
    caused_by_execution_attempt_ref: str = Field(min_length=1, max_length=260)
    observed_effect_refs: tuple[str, ...]
    intended_mitigation: str = Field(min_length=1, max_length=5_000)
    residual_effect_expectation: str = Field(min_length=1, max_length=5_000)
    evidence_refs: tuple[str, ...]


class MergeProposeInput(ProjectInput):
    action_ids: tuple[str, ...] = Field(min_length=2)
    field_mapping: dict[str, JsonValue]
    evidence_refs: tuple[str, ...]
    rationale: str = Field(min_length=1, max_length=5_000)
    expected_revision_digests: tuple[str, ...] = Field(min_length=2)
