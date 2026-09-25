from __future__ import annotations

from thoth.domain.enums import EffectObservation, ExecutionAttemptState, OutcomeValidity
from thoth.domain.execution import Execution, ExecutionReconciliation


def reconcile_execution(value: ExecutionReconciliation) -> Execution:
    execution = value.execution
    if execution.state != ExecutionAttemptState.UNKNOWN_COMPLETION:
        return execution
    if value.effect_observation == EffectObservation.CONFIRMED:
        return execution.model_copy(
            update={
                "state": ExecutionAttemptState.SUCCEEDED,
                "output_refs": value.authoritative_output_refs,
                "outcome_validity": OutcomeValidity.NOT_ASSESSED,
            }
        )
    if value.effect_observation == EffectObservation.ABSENT:
        return execution.model_copy(
            update={
                "state": ExecutionAttemptState.FAILED,
                "output_refs": value.authoritative_output_refs,
                "outcome_validity": OutcomeValidity.NOT_ASSESSED,
            }
        )
    return execution
