"""Canonical execution scope, checkpoints and attempt transition preparation."""

from thoth.domain.action_full import ActionPlanRecord
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import CutoffState
from thoth.domain.execution_full import PlanExecutionRecord
from thoth.domain.sandbox import SandboxExecutionState, SandboxReceipt, SandboxResult
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.runtime import ClockPort, IdGeneratorPort


def execution_checkpoint(
    execution_id: str,
    plan_digest: str,
    revision: int,
    executable: tuple[str, ...],
    blocked: dict[str, str],
    protected: tuple[str, ...],
    selected_step_ids: tuple[str, ...] = (),
) -> str:
    payload: dict[str, object] = {
        "execution_id": execution_id,
        "plan_digest": plan_digest,
        "revision": revision,
        "executable": executable,
        "blocked": blocked,
        "protected": protected,
    }
    if selected_step_ids:
        payload["selected_step_ids"] = selected_step_ids
    return domain_digest("EXECUTION_CHECKPOINT", "1.0.0", canonical_payload(payload))


def execution_resume_token(checkpoint: str, execution_id: str) -> str:
    return domain_digest(
        "EXECUTION_RESUME_TOKEN",
        "1.0.0",
        canonical_payload({"value": f"{execution_id}:{checkpoint}"}),
    )


def execution_digest(value: PlanExecutionRecord) -> str:
    return domain_digest(
        "PLAN_EXECUTION",
        "1.0.0",
        canonical_payload(value.model_dump(mode="python", exclude={"revision_digest"})),
    )


def prepare_execution_record(
    *,
    project_id: str,
    plan: ActionPlanRecord,
    execution_profile_ref: str,
    expected_working_head_digest: str,
    selected_step_ids: tuple[str, ...],
    frontier: tuple[tuple[str, ...], dict[str, str], tuple[str, ...], tuple[str, ...]],
    clock: ClockPort,
    ids: IdGeneratorPort,
) -> PlanExecutionRecord:
    executable, blocked, protected, prohibited = frontier
    execution_id = ids.new("plan-execution")
    checkpoint = execution_checkpoint(
        execution_id, plan.revision_digest, 0, executable, blocked, protected, selected_step_ids
    )
    record = PlanExecutionRecord(
        execution_revision_id=ids.new("execution-revision"),
        plan_execution_id=execution_id,
        project_id=project_id,
        object_id=plan.object_id,
        plan_id=plan.plan_id,
        plan_revision_digest=plan.revision_digest,
        expected_working_head_digest=expected_working_head_digest,
        execution_profile_ref=execution_profile_ref,
        state="RUNNING" if executable else "PAUSED_PROTECTED_BOUNDARY",
        executable_steps=executable,
        blocked_steps=blocked,
        protected_steps=protected,
        prohibited_steps=prohibited,
        selected_step_ids=selected_step_ids,
        completed_steps=(),
        attempt_refs=(),
        checkpoint_digest=checkpoint,
        resume_token=execution_resume_token(checkpoint, execution_id),
        revision=0,
        created_at=clock.now(),
        updated_at=clock.now(),
        revision_digest="0" * 64,
    )
    return record.model_copy(update={"revision_digest": execution_digest(record)})


def sandbox_attempt_updates(
    result: SandboxResult,
    receipt: SandboxReceipt,
    observation_refs: tuple[str, ...],
    artifacts: ArtifactLedgerPort,
    *,
    observation_admitted: bool,
) -> dict[str, object]:
    if (receipt.project_id, receipt.attempt_id, receipt.result_state) != (
        result.project_id,
        result.attempt_id,
        result.state,
    ):
        raise ValueError("EXECUTION_RESULT_RECEIPT_MISMATCH")
    spans = tuple(artifacts.read_evidence(reference) for reference in observation_refs)
    if any(
        span is None
        or span.project_id != result.project_id
        or (observation_admitted and span.cutoff_state != CutoffState.ELIGIBLE)
        for span in spans
    ):
        raise ValueError("EXECUTION_OBSERVATION_SCOPE_MISMATCH")
    state = {
        SandboxExecutionState.SUCCEEDED: "SUCCEEDED",
        SandboxExecutionState.CANCELLED: "CANCELLED_CONFIRMED",
    }.get(result.state, "FAILED")
    failure = None
    if state == "FAILED":
        failure = (
            "TRANSIENT"
            if result.state
            in {
                SandboxExecutionState.BOOT_FAILED,
                SandboxExecutionState.TIMED_OUT,
                SandboxExecutionState.OOM_KILLED,
            }
            else "TERMINAL"
        )
    return {
        "state": state,
        "failure_class": failure,
        "output_refs": (f"sandbox-receipt:{receipt.attempt_id}", *observation_refs),
        "output_digests": (
            *result.output_digests,
            *(span.text_sha256 for span in spans if span is not None),
        ),
        "error": None
        if state == "SUCCEEDED"
        else {
            "sandbox_state": result.state.value,
            "detail": result.failure_detail or result.state.value,
        },
        "effect_state": "NONE",
        "observation_completeness": "NOT_ADMITTED"
        if not observation_admitted
        else "COMPLETE"
        if observation_refs
        else "MISSING",
        "observation_refs": observation_refs,
        "heartbeat_at": result.completed_at,
        "completed_at": result.completed_at,
    }
