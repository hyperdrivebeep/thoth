from __future__ import annotations

from pydantic import AwareDatetime, Field, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.ids import ProjectId, Sha256


class PlanExecutionRecord(DomainModel):
    execution_revision_id: str
    plan_execution_id: str
    project_id: ProjectId
    object_id: str
    plan_id: str
    plan_revision_digest: Sha256
    expected_working_head_digest: Sha256
    execution_profile_ref: str
    state: str = "CREATED"
    executable_steps: tuple[str, ...] = ()
    blocked_steps: dict[str, str] = Field(default_factory=dict)
    protected_steps: tuple[str, ...] = ()
    prohibited_steps: tuple[str, ...] = ()
    completed_steps: tuple[str, ...] = ()
    selected_step_ids: tuple[str, ...] = ()
    attempt_refs: tuple[str, ...] = ()
    effect_refs: tuple[str, ...] = ()
    observation_refs: tuple[str, ...] = ()
    reconciliation_refs: tuple[str, ...] = ()
    compensation_refs: tuple[str, ...] = ()
    checkpoint_digest: Sha256
    resume_token: Sha256
    cause_revision_ref: str | None = None
    invalidation_impact_refs: tuple[str, ...] = ()
    revision: int = 0
    revision_digest: Sha256
    supersedes_revision_digest: Sha256 | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    schema_version: str = "1.0.0"


class StepExecutionAttemptRecord(DomainModel):
    attempt_revision_id: str
    attempt_id: str
    project_id: ProjectId
    plan_execution_id: str
    plan_id: str
    plan_revision_digest: Sha256
    step_id: str
    attempt_number: int = Field(ge=1)
    state: str
    exact_invocation_digest: Sha256
    input_digests: tuple[Sha256, ...]
    target_digests: tuple[Sha256, ...]
    environment_digest: Sha256
    authorization_digest: Sha256 | None = None
    idempotency_key: str
    delivery_guarantee: str
    failure_class: str | None = None
    output_refs: tuple[str, ...] = ()
    output_digests: tuple[Sha256, ...] = ()
    error: dict[str, object] | None = None
    effect_state: str = "NONE"
    observation_completeness: str = "MISSING"
    observation_refs: tuple[str, ...] = ()
    heartbeat_at: AwareDatetime | None = None
    started_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    revision_digest: Sha256
    supersedes_revision_digest: Sha256 | None = None
    created_at: AwareDatetime
    schema_version: str = "1.0.0"

    @model_validator(mode="after")
    def success_is_not_outcome_validity(self) -> StepExecutionAttemptRecord:
        if self.state == "SUCCEEDED" and self.observation_completeness == "COMPLETE":
            return self
        return self


class ExecutionEffectRecord(DomainModel):
    effect_id: str
    project_id: ProjectId
    attempt_id: str
    effect_state: str
    effect_refs: tuple[str, ...]
    target_reconciliation_required: bool
    compensation_refs: tuple[str, ...] = ()
    residual_effect_context: str | None = None
    effect_digest: Sha256
    created_at: AwareDatetime
    schema_version: str = "1.0.0"


class ReconciliationRecord(DomainModel):
    reconciliation_id: str
    project_id: ProjectId
    plan_execution_id: str
    attempt_id: str
    method: str
    target_state_evidence_refs: tuple[str, ...]
    finding: str
    duplicate_risk: str
    retry_permitted: bool
    next_permitted_action: str
    reconciliation_digest: Sha256
    created_at: AwareDatetime
    schema_version: str = "1.0.0"


class ExecutionAuditRecord(DomainModel):
    audit_id: str
    project_id: ProjectId
    plan_execution_id: str
    event_type: str
    payload: dict[str, object]
    event_digest: Sha256
    created_at: AwareDatetime
    schema_version: str = "1.0.0"
