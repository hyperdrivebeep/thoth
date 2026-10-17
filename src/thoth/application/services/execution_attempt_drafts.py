"""Pure construction of Execution attempt records from an already authorized step.

The service decides whether an attempt may be created: plan currentness, risk tier and, for R3,
the approved-authorization lookup and its consumption. This module only assembles the record from
those results, calling the service's own id and clock ports in the original order.
"""

from __future__ import annotations

from typing import cast

from thoth.application.services.execution_record_drafts import record_digest
from thoth.domain.action_full import ActionPlanRecord, AuthorizationEnvelopeRecord
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.execution_full import PlanExecutionRecord, StepExecutionAttemptRecord
from thoth.ports.runtime import ClockPort, IdGeneratorPort


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, tuple | list):
        return ()
    return tuple(str(item) for item in cast(tuple[object, ...] | list[object], value))


def attempt_record(
    execution: PlanExecutionRecord,
    plan: ActionPlanRecord,
    step_id: str,
    *,
    step: dict[str, object],
    risk: str,
    authorization: AuthorizationEnvelopeRecord | None,
    attempt_number: int,
    ids: IdGeneratorPort,
    clock: ClockPort,
) -> StepExecutionAttemptRecord:
    input_values = _strings(step.get("input_digests", step.get("inputs", ())))
    input_digests = tuple(
        value
        if len(value) == 64
        else domain_digest("EXECUTION_INPUT", "1.0.0", canonical_payload({"value": value}))
        for value in input_values
    )
    target_values = _strings(step.get("target_digests", ()))
    target_digests = tuple(
        value
        if len(value) == 64
        else domain_digest("EXECUTION_TARGET", "1.0.0", canonical_payload({"value": value}))
        for value in target_values
    )
    environment = {
        "execution_profile_ref": execution.execution_profile_ref,
        "risk_tier": risk,
        "sandbox": risk == "R2",
    }
    environment_digest = domain_digest(
        "EXECUTION_ENVIRONMENT", "1.0.0", canonical_payload(environment)
    )
    invocation = {
        "plan_revision_digest": plan.revision_digest,
        "step_id": step_id,
        "input_digests": input_digests,
        "target_digests": target_digests,
        "environment_digest": environment_digest,
        "authorization_digest": (
            None if authorization is None else authorization.exact_scope_digest
        ),
        "attempt_number": attempt_number,
    }
    draft: dict[str, object] = {
        "attempt_revision_id": ids.new("attempt-revision"),
        "attempt_id": ids.new("execution-attempt"),
        "project_id": execution.project_id,
        "plan_execution_id": execution.plan_execution_id,
        "plan_id": plan.plan_id,
        "plan_revision_digest": plan.revision_digest,
        "step_id": step_id,
        "attempt_number": attempt_number,
        "state": "DISPATCHED",
        "exact_invocation_digest": domain_digest(
            "EXECUTION_INVOCATION", "1.0.0", canonical_payload(invocation)
        ),
        "input_digests": input_digests,
        "target_digests": target_digests,
        "environment_digest": environment_digest,
        "authorization_digest": (
            None if authorization is None else authorization.exact_scope_digest
        ),
        "idempotency_key": f"{execution.plan_execution_id}:{step_id}:{attempt_number}",
        "delivery_guarantee": (
            "IDEMPOTENT_RETRYABLE" if risk in {"R0", "R1", "R2"} else "REQUIRES_RECONCILIATION"
        ),
        "effect_state": "POSSIBLE" if risk == "R3" else "NONE",
        "observation_completeness": "MISSING",
        "heartbeat_at": clock.now(),
        "started_at": clock.now(),
        "created_at": clock.now(),
    }
    return StepExecutionAttemptRecord.model_validate(
        {**draft, "revision_digest": record_digest("STEP_EXECUTION_ATTEMPT", draft)}
    )


def revised_attempt_record(
    current: StepExecutionAttemptRecord,
    updates: dict[str, object],
    *,
    ids: IdGeneratorPort,
    clock: ClockPort,
) -> StepExecutionAttemptRecord:
    draft = current.model_dump(mode="python")
    draft.update(updates)
    draft.update(
        {
            "attempt_revision_id": ids.new("attempt-revision"),
            "supersedes_revision_digest": current.revision_digest,
            "created_at": clock.now(),
        }
    )
    draft.pop("revision_digest", None)
    return StepExecutionAttemptRecord.model_validate(
        {**draft, "revision_digest": record_digest("STEP_EXECUTION_ATTEMPT", draft)}
    )
