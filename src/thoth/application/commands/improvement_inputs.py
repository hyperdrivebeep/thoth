"""Input contracts for improvement_full; handlers and state transitions remain in their owner."""

from __future__ import annotations

from pydantic import Field, JsonValue

from thoth.domain.base import DomainModel


class ProjectInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)


class ListInput(ProjectInput):
    target_component: str | None = Field(default=None, max_length=160)
    candidate_lifecycle: str | None = Field(default=None, max_length=80)
    exposure_state: str | None = Field(default=None, max_length=80)
    promotion_state: str | None = Field(default=None, max_length=80)


class ImprovementReadInput(ProjectInput):
    improvement_revision_id: str = Field(min_length=1, max_length=160)
    revision_digest: str | None = Field(default=None, min_length=64, max_length=64)


class RecordReadInput(ProjectInput):
    record_id: str = Field(min_length=1, max_length=160)


class ProposeInput(ProjectInput):
    thread_id: str | None = Field(default=None, min_length=1, max_length=160)
    target_component: str = Field(min_length=1, max_length=160)
    scope_key: str = Field(min_length=1, max_length=500)
    trigger_refs: tuple[str, ...]
    baseline_digest: str = Field(min_length=64, max_length=64)
    candidate_content: dict[str, JsonValue]
    improvement_hypothesis: str = Field(min_length=1, max_length=5_000)
    evaluation_contract_ref: str = Field(min_length=1, max_length=160)


class ReviseInput(ImprovementReadInput):
    patch: dict[str, JsonValue]
    evidence_refs: tuple[str, ...]
    reason: str = Field(min_length=1, max_length=2_000)
    expected_revision_digest: str = Field(min_length=64, max_length=64)


class EvaluationPlanInput(ProjectInput):
    improvement_revision_id: str = Field(min_length=1, max_length=160)
    baseline_digest: str = Field(min_length=64, max_length=64)
    datasets: tuple[dict[str, JsonValue], ...]
    evaluator_refs: tuple[str, ...]
    guardrails: tuple[dict[str, JsonValue], ...]
    exposure_policy_ref: str = Field(min_length=1, max_length=160)


class EvaluationAssessInput(ProjectInput):
    evaluation_plan_id: str = Field(min_length=1, max_length=160)
    result_refs: tuple[str, ...]
    evaluator_result_refs: tuple[str, ...]
    exposure_ledger_ref: str = Field(min_length=1, max_length=160)
    expected_evaluation_revision: int = Field(ge=1)


class ExposurePrepareInput(ProjectInput):
    improvement_revision_id: str = Field(min_length=1, max_length=160)
    requested_exposure_state: str = Field(pattern=r"^(OFFLINE|SANDBOX|SHADOW|CANARY)$")
    evaluation_plan_id: str = Field(min_length=1, max_length=160)
    scope: dict[str, str]
    duration_budget: dict[str, JsonValue]
    stop_rollback_contract: dict[str, JsonValue]


class PromotionPrepareInput(ProjectInput):
    improvement_revision_id: str = Field(min_length=1, max_length=160)
    assessment_refs: tuple[str, ...]
    candidate_digest: str = Field(min_length=64, max_length=64)
    baseline_digest: str = Field(min_length=64, max_length=64)
    target_scope: str = Field(min_length=1, max_length=500)
    policy_version: str = Field(min_length=1, max_length=160)


class PromotionDecideInput(ProjectInput):
    promotion_candidate_id: str = Field(min_length=1, max_length=160)
    decision: str = Field(pattern=r"^(APPROVE|REJECT)$")
    actor_ref: str = Field(min_length=1, max_length=160)
    role_assignment_ref: str = Field(min_length=1, max_length=160)
    approved_digest: str = Field(min_length=64, max_length=64)
    reason: str | None = Field(default=None, max_length=2_000)


class RollbackPrepareInput(ProjectInput):
    promoted_revision_ref: str = Field(min_length=1, max_length=160)
    guardrail_failure_refs: tuple[str, ...]
    rollback_target_digest: str = Field(min_length=64, max_length=64)
    target_scope: str = Field(min_length=1, max_length=500)


class RetireInput(ImprovementReadInput):
    reason: str = Field(min_length=1, max_length=2_000)
    evidence_refs: tuple[str, ...]
    expected_revision_digest: str = Field(min_length=64, max_length=64)
