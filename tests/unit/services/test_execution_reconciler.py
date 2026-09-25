from __future__ import annotations

from datetime import UTC, datetime

from thoth.application.services import reconcile_execution
from thoth.domain.enums import (
    EffectObservation,
    ExecutionAttemptState,
    OutcomeValidity,
    RiskTier,
)
from thoth.domain.execution import Execution, ExecutionReconciliation


def _unknown() -> Execution:
    return Execution(
        execution_id="execution:unknown",
        project_id="project:1",
        action_id="action:1",
        attempt=1,
        risk_tier=RiskTier.R3,
        state=ExecutionAttemptState.UNKNOWN_COMPLETION,
        input_digests=("a" * 64,),
        environment_ref="field",
        started_at=datetime(2026, 8, 30, 12, 0, tzinfo=UTC),
    )


def test_confirmed_external_effect_reconciles_execution_but_not_outcome_validity() -> None:
    result = reconcile_execution(
        ExecutionReconciliation(
            execution=_unknown(),
            effect_observation=EffectObservation.CONFIRMED,
            authoritative_output_refs=("field-receipt:1",),
            reason="field system confirms the effect",
        )
    )

    assert result.state == ExecutionAttemptState.SUCCEEDED
    assert result.outcome_validity == OutcomeValidity.NOT_ASSESSED
    assert result.output_refs == ("field-receipt:1",)


def test_unknown_effect_remains_unknown_completion() -> None:
    result = reconcile_execution(
        ExecutionReconciliation(
            execution=_unknown(),
            effect_observation=EffectObservation.UNKNOWN,
            reason="no authoritative observation is available",
        )
    )

    assert result.state == ExecutionAttemptState.UNKNOWN_COMPLETION
