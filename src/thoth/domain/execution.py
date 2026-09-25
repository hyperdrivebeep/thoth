from __future__ import annotations

from pydantic import AwareDatetime, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.enums import (
    EffectObservation,
    ExecutionAttemptState,
    OutcomeValidity,
    RiskTier,
)
from thoth.domain.ids import ActionId, ExecutionId, ProjectId, Sha256


class Execution(DomainModel):
    execution_id: ExecutionId
    project_id: ProjectId
    action_id: ActionId
    attempt: int
    risk_tier: RiskTier
    state: ExecutionAttemptState
    input_digests: tuple[Sha256, ...]
    environment_ref: str
    output_refs: tuple[str, ...] = ()
    log_refs: tuple[str, ...] = ()
    outcome_validity: OutcomeValidity = OutcomeValidity.NOT_ASSESSED
    started_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    schema_version: str = "1.0.0"

    @model_validator(mode="after")
    def success_does_not_imply_valid_outcome(self) -> Execution:
        if (
            self.state == ExecutionAttemptState.SUCCEEDED
            and self.outcome_validity == OutcomeValidity.VALID
            and not self.output_refs
        ):
            raise ValueError(
                "valid outcome requires independently assessable output references"
            )
        return self


class ExecutionReconciliation(DomainModel):
    execution: Execution
    effect_observation: EffectObservation
    authoritative_output_refs: tuple[str, ...] = ()
    reason: str
