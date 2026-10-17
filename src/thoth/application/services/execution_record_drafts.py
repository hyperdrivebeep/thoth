"""Pure construction of Execution revision, reconciliation, effect and compensation values.

Each function builds a value from its arguments. The id and clock arguments are the service's own
port objects and are called in the order the service called them. Nothing here reads or writes
storage, opens a transaction, checks a guard or decides retry policy; those stay in the service.
"""

from __future__ import annotations

from typing import cast

from thoth.application.services.execution_preparation import (
    execution_checkpoint,
    execution_resume_token,
)
from thoth.domain.action_full import ActionPlanRecord
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.execution_full import (
    ExecutionEffectRecord,
    PlanExecutionRecord,
    ReconciliationRecord,
    StepExecutionAttemptRecord,
)
from thoth.ports.runtime import ClockPort, IdGeneratorPort

# Reconciliation method -> (finding, effect state, retry permitted, next permitted action).
RECONCILIATION_FINDINGS: dict[str, tuple[str, str, bool, str]] = {
    "TARGET_CHECK_CONFIRMED_NOT_APPLIED": (
        "CONFIRMED_NOT_APPLIED",
        "NONE",
        True,
        "RETRY_ALLOWED",
    ),
    "TARGET_CHECK_CONFIRMED_APPLIED": (
        "CONFIRMED_APPLIED",
        "CONFIRMED",
        False,
        "LINK_OBSERVATION",
    ),
    "TARGET_CHECK_PARTIAL": (
        "PARTIAL",
        "PARTIAL",
        False,
        "COMPENSATION_OR_NEW_REVISION",
    ),
    "TARGET_CHECK_STILL_UNKNOWN": (
        "STILL_UNKNOWN",
        "EFFECT_MAY_CONTINUE",
        False,
        "RECONCILE_AGAIN",
    ),
}
# Finding -> attempt state.
RECONCILIATION_ATTEMPT_STATES: dict[str, str] = {
    "CONFIRMED_NOT_APPLIED": "FAILED",
    "CONFIRMED_APPLIED": "SUCCEEDED",
    "PARTIAL": "PARTIAL",
    "STILL_UNKNOWN": "UNKNOWN_COMPLETION",
}


def record_digest(kind: str, value: dict[str, object]) -> str:
    return domain_digest(kind, "1.0.0", canonical_payload(value))


def revised_execution_record(
    current: PlanExecutionRecord,
    updates: dict[str, object],
    *,
    ids: IdGeneratorPort,
    clock: ClockPort,
) -> PlanExecutionRecord:
    draft = current.model_dump(mode="python")
    draft.update(updates)
    draft.update(
        {
            "execution_revision_id": ids.new("execution-revision"),
            "revision": current.revision + 1,
            "supersedes_revision_digest": current.revision_digest,
            "updated_at": clock.now(),
        }
    )
    draft.pop("revision_digest", None)
    checkpoint = execution_checkpoint(
        current.plan_execution_id,
        current.plan_revision_digest,
        current.revision + 1,
        tuple(cast(tuple[str, ...], draft.get("executable_steps", ()))),
        cast(dict[str, str], draft.get("blocked_steps", {})),
        tuple(cast(tuple[str, ...], draft.get("protected_steps", ()))),
        current.selected_step_ids,
    )
    draft["checkpoint_digest"] = checkpoint
    draft["resume_token"] = execution_resume_token(checkpoint, current.plan_execution_id)
    return PlanExecutionRecord.model_validate(
        {**draft, "revision_digest": record_digest("PLAN_EXECUTION", draft)}
    )


def reconciliation_record(
    current: PlanExecutionRecord,
    attempt: StepExecutionAttemptRecord,
    *,
    method: str,
    evidence_refs: tuple[str, ...],
    finding: str,
    retry: bool,
    next_action: str,
    ids: IdGeneratorPort,
    clock: ClockPort,
) -> ReconciliationRecord:
    reconciliation_draft: dict[str, object] = {
        "reconciliation_id": ids.new("reconciliation"),
        "project_id": current.project_id,
        "plan_execution_id": current.plan_execution_id,
        "attempt_id": attempt.attempt_id,
        "method": method,
        "target_state_evidence_refs": evidence_refs,
        "finding": finding,
        "duplicate_risk": ("NONE_CONFIRMED" if finding == "CONFIRMED_NOT_APPLIED" else "POSSIBLE"),
        "retry_permitted": retry,
        "next_permitted_action": next_action,
        "created_at": clock.now(),
    }
    return ReconciliationRecord.model_validate(
        {
            **reconciliation_draft,
            "reconciliation_digest": record_digest(
                "EXECUTION_RECONCILIATION", reconciliation_draft
            ),
        }
    )


def effect_record(
    current: PlanExecutionRecord,
    attempt: StepExecutionAttemptRecord,
    *,
    effect_state: str,
    evidence_refs: tuple[str, ...],
    finding: str,
    ids: IdGeneratorPort,
    clock: ClockPort,
) -> ExecutionEffectRecord:
    effect_draft: dict[str, object] = {
        "effect_id": ids.new("execution-effect"),
        "project_id": current.project_id,
        "attempt_id": attempt.attempt_id,
        "effect_state": effect_state,
        "effect_refs": evidence_refs,
        "target_reconciliation_required": finding == "STILL_UNKNOWN",
        "created_at": clock.now(),
    }
    return ExecutionEffectRecord.model_validate(
        {**effect_draft, "effect_digest": record_digest("EXECUTION_EFFECT", effect_draft)}
    )


def compensation_specification(
    reason: str, attempt: StepExecutionAttemptRecord, effect_refs: tuple[str, ...]
) -> dict[str, object]:
    return {
        "description": reason,
        "caused_by_execution_attempt_ref": attempt.attempt_id,
        "observed_effect_refs": effect_refs,
        "expected_observation_or_change": {
            "description": "residual effect is reduced and remains observable"
        },
        "effect_completeness_confirmed": False,
        "effect_vector": {"effect_completeness_confirmed": False},
        "stop_conditions": ("residual effect boundary reached",),
        "observability": "effect and residual evidence required",
    }


def advance_state(
    plan: ActionPlanRecord,
    current: PlanExecutionRecord,
    executable: tuple[str, ...],
    protected: tuple[str, ...],
) -> str:
    all_steps = set(current.selected_step_ids) or {str(step["step_id"]) for step in plan.steps}
    completed = set(current.completed_steps)
    return (
        "COMPLETED"
        if completed == all_steps
        else "RUNNING"
        if executable
        else "PAUSED_PROTECTED_BOUNDARY"
        if protected
        else "PARTIAL"
    )


def sandbox_not_admitted_updates(
    current: PlanExecutionRecord,
    step_id: str,
    observation_refs: tuple[str, ...],
    admission_reason: str,
) -> dict[str, object]:
    return {
        "state": "PARTIAL",
        "blocked_steps": {
            **current.blocked_steps,
            step_id: admission_reason,
        },
        "observation_refs": tuple(dict.fromkeys((*current.observation_refs, *observation_refs))),
    }


def sandbox_failed_updates(
    current: PlanExecutionRecord, step_id: str, result_state: str
) -> dict[str, object]:
    return {
        "state": "PARTIAL",
        "blocked_steps": {
            **current.blocked_steps,
            step_id: result_state,
        },
    }


def sandbox_succeeded_updates(
    current: PlanExecutionRecord,
    step_id: str,
    receipt_digest: str,
    observation_refs: tuple[str, ...],
) -> dict[str, object]:
    return {
        "completed_steps": tuple(dict.fromkeys((*current.completed_steps, step_id))),
        "effect_refs": tuple(dict.fromkeys((*current.effect_refs, receipt_digest))),
        "observation_refs": tuple(dict.fromkeys((*current.observation_refs, *observation_refs))),
    }
