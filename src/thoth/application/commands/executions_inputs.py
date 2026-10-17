"""Input contracts for execution RPC handlers; no execution or persistence logic."""

from __future__ import annotations

from pydantic import Field, JsonValue

from thoth.domain.base import DomainModel


class ProjectInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)


class ExecutionListInput(ProjectInput):
    object_id: str | None = Field(default=None, max_length=160)
    plan_id: str | None = Field(default=None, max_length=160)
    plan_state: str | None = Field(default=None, max_length=80)


class ExecutionReadInput(ProjectInput):
    plan_execution_id: str = Field(min_length=1, max_length=160)


class StepInput(ExecutionReadInput):
    step_id: str = Field(min_length=1, max_length=160)


class AttemptListInput(ExecutionReadInput):
    step_id: str | None = Field(default=None, max_length=160)
    attempt_state: str | None = Field(default=None, max_length=80)


class AttemptReadInput(ProjectInput):
    attempt_id: str = Field(min_length=1, max_length=160)


class ReconciliationReadInput(ProjectInput):
    reconciliation_id: str = Field(min_length=1, max_length=160)


class StartInput(ProjectInput):
    plan_id: str = Field(min_length=1, max_length=160)
    plan_revision_digest: str = Field(min_length=64, max_length=64)
    execution_profile_ref: str = Field(min_length=1, max_length=160)
    expected_working_head_digest: str = Field(min_length=64, max_length=64)


class RevisionBoundInput(ExecutionReadInput):
    expected_execution_revision: int = Field(ge=0)


class PauseInput(RevisionBoundInput):
    reason: str = Field(min_length=1, max_length=2_000)


class ResumeInput(RevisionBoundInput):
    checkpoint_digest: str = Field(min_length=64, max_length=64)


class CancelInput(RevisionBoundInput):
    scope: str = Field(min_length=1, max_length=260)
    reason: str = Field(min_length=1, max_length=2_000)


class RetryInput(RevisionBoundInput):
    step_id: str = Field(min_length=1, max_length=160)
    failed_attempt_id: str = Field(min_length=1, max_length=160)
    retry_reason: str = Field(min_length=1, max_length=2_000)


class ReconcileInput(ExecutionReadInput):
    attempt_id: str = Field(min_length=1, max_length=160)
    target_state_evidence_refs: tuple[str, ...]
    reconciliation_method: str = Field(min_length=1, max_length=160)


class ObservationLinkInput(ProjectInput):
    attempt_id: str = Field(min_length=1, max_length=160)
    observation_refs: tuple[str, ...]
    completeness: str = Field(pattern=r"^(COMPLETE|PARTIAL|MISSING|CORRUPT|NOT_APPLICABLE)$")
    evidence_refs: tuple[str, ...]
    expected_execution_revision: int = Field(ge=0)


class CompensationInput(ProjectInput):
    attempt_id: str = Field(min_length=1, max_length=160)
    effect_refs: tuple[str, ...]
    reason: str = Field(min_length=1, max_length=2_000)


class InvalidateInput(ExecutionReadInput):
    cause_revision_ref: str = Field(min_length=1, max_length=260)
    impact_refs: tuple[str, ...]
    # Clearing an unsettled sandbox result so the same work may run again. The execution revision
    # that was read is required then, and the earlier attempt and its result are kept.
    clear_pending_attempt_id: str | None = Field(default=None, min_length=1, max_length=160)
    expected_execution_revision: int | None = Field(default=None, ge=0)
    reason: str | None = Field(default=None, min_length=1, max_length=2_000)
    input_difference: dict[str, JsonValue] | None = None
