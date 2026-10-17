"""Projection dictionaries the R2 closed loop reports (98).

Each function turns values the coordinator already holds into the JSON projection of one terminal
state: completed, test hold, template required, sandbox failure, pre-I/O block, admission policy
change, stale head and failure with recovery. They read nothing and write nothing; the coordinator
wraps the result in its own execution record and keeps every ledger, sandbox, research and
lifecycle call, await and transaction in place.
"""

from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from thoth.application.services.r2_outcome_projection import ProcessOutcomeProjection
from thoth.application.services.r2_test_lifecycle import PendingResultHold, ResearchTestCompletion
from thoth.application.services.revision_service import CommitResult
from thoth.application.services.sandbox_service import SandboxExecutionBundle
from thoth.domain.outcome import OutcomeRecord
from thoth.domain.policy import PolicyDenialReceipt
from thoth.domain.recovery import ReconciliationRecord, RecoveryAttempt
from thoth.domain.sandbox import SandboxFailure, SandboxRunSpec


def completed_projection(
    test_completion: ResearchTestCompletion | None,
    spec: SandboxRunSpec,
    bundle: SandboxExecutionBundle,
    process: ProcessOutcomeProjection,
    outcome: OutcomeRecord,
    commit: CommitResult,
    attempts: tuple[RecoveryAttempt, ...],
    retry_ordinal: int,
) -> dict[str, JsonValue]:
    return {
        **({} if test_completion is None else {"test_lifecycle": test_completion.projection}),
        "terminal_state": "COMPLETED",
        "compiled_spec": cast(JsonValue, spec.model_dump(mode="json")),
        "sandbox_result": cast(JsonValue, bundle.result.model_dump(mode="json")),
        "sandbox_receipt": cast(JsonValue, bundle.receipt.model_dump(mode="json")),
        "observation_refs": list(process.observation_refs),
        "outcome": cast(JsonValue, outcome.model_dump(mode="json")),
        "outcome_validity": cast(JsonValue, process.validity.model_dump(mode="json")),
        "hypothesis_appraisal": cast(JsonValue, process.appraisal.model_dump(mode="json")),
        "next_action_order": list(process.next_order),
        "non_truth_receipt": cast(JsonValue, commit.receipt.model_dump(mode="json")),
        "scientific_truth_state": "NOT_CERTIFIED",
        "recovery": {
            "attempts": [item.model_dump(mode="json") for item in attempts],
            "retry_count": retry_ordinal,
            "baseline_head_before": spec.current_head_set_digest,
            "baseline_head_after": commit.after_head_set_digest,
            "candidate_branch_revision_ids": [],
            "repair_basis": None,
            "reconciliation": None,
            "retry_budget_exhausted": False,
            "failure_receipt": commit.receipt.model_dump(mode="json"),
        },
    }


def prepare_blocked_projection(failure: SandboxFailure) -> dict[str, JsonValue]:
    return {
        "terminal_state": "POLICY_BLOCKED" if failure.policy_denial is not None else "HOLD",
        "reason_code": failure.code.value,
        "stage": "PRE_IO",
        "runtime_io_completed": False,
        "policy_denial": None
        if failure.policy_denial is None
        else cast(JsonValue, failure.policy_denial.model_dump(mode="json")),
        "scientific_truth_state": "NOT_CERTIFIED",
    }


def hold_reason_code(error: ValueError) -> str:
    reason = str(error)
    code = (
        reason
        if reason
        and len(reason) <= 160
        and all(
            character.isupper() or character.isdigit() or character == "_" for character in reason
        )
        else "RESEARCH_TEST_INPUT_INVALID"
    )
    return code


def hold_projection(
    code: str, error: ValueError, bundle: SandboxExecutionBundle | None
) -> dict[str, JsonValue]:
    return {
        "terminal_state": "HOLD",
        "reason_code": code,
        "runtime_io_completed": bundle is not None,
        "scientific_truth_state": "NOT_CERTIFIED",
        "sandbox_receipt": None
        if bundle is None
        else cast(JsonValue, bundle.receipt.model_dump(mode="json")),
        **(
            {"pending_result": cast(JsonValue, error.detail)}
            if isinstance(error, PendingResultHold)
            else {}
        ),
    }


def template_required_projection(action_id: str) -> dict[str, JsonValue]:
    return {
        "terminal_state": "HOLD",
        "reason_code": "SANDBOX_TEMPLATE_REQUIRED",
        "action_id": action_id,
        "scientific_truth_state": "NOT_CERTIFIED",
    }


def sandbox_failure_projection(failure: SandboxFailure, action_id: str) -> dict[str, JsonValue]:
    return {
        "terminal_state": ("POLICY_BLOCKED" if failure.policy_denial is not None else "HOLD"),
        "action_id": action_id,
        "policy_denial": (
            None
            if failure.policy_denial is None
            else cast(JsonValue, failure.policy_denial.model_dump(mode="json"))
        ),
        "scientific_truth_state": "NOT_CERTIFIED",
    }


def admission_policy_projection(
    action_id: str, denial: PolicyDenialReceipt, bundle: SandboxExecutionBundle
) -> dict[str, JsonValue]:
    return {
        "terminal_state": "POLICY_BLOCKED",
        "reason_code": "SANDBOX_ADMISSION_POLICY_CHANGED",
        "stage": "POST_IO_ADMISSION",
        "runtime_io_completed": True,
        "action_id": action_id,
        "scientific_truth_state": "NOT_CERTIFIED",
        "policy_denial": cast(JsonValue, denial.model_dump(mode="json")),
        "sandbox_result": cast(JsonValue, bundle.result.model_dump(mode="json")),
    }


def stale_head_projection(action_id: str, bundle: SandboxExecutionBundle) -> dict[str, JsonValue]:
    return {
        "terminal_state": "HOLD",
        "reason_code": "SANDBOX_HEAD_STALE_AFTER_IO",
        "action_id": action_id,
        "sandbox_result": cast(JsonValue, bundle.result.model_dump(mode="json")),
        "sandbox_receipt": cast(JsonValue, bundle.receipt.model_dump(mode="json")),
        "scientific_truth_state": "NOT_CERTIFIED",
    }


def failure_projection(
    terminal: str,
    test_completion: ResearchTestCompletion | None,
    action_id: str,
    attempts: tuple[RecoveryAttempt, ...],
    baseline_digest: str,
    baseline_after: str,
    commit: CommitResult,
    repair_basis: str,
    reconciliation: ReconciliationRecord | None,
    retry_budget_exhausted: bool,
) -> dict[str, JsonValue]:
    return {
        "terminal_state": terminal,
        **({} if test_completion is None else {"test_lifecycle": test_completion.projection}),
        "action_id": action_id,
        "scientific_truth_state": "NOT_CERTIFIED",
        "recovery": {
            "attempts": [item.model_dump(mode="json") for item in attempts],
            "retry_count": max(0, len(attempts) - 1),
            "baseline_head_before": baseline_digest,
            "baseline_head_after": baseline_after,
            "candidate_branch_revision_ids": list(commit.branch_revision_ids),
            "repair_basis": repair_basis,
            "reconciliation": (
                None if reconciliation is None else reconciliation.model_dump(mode="json")
            ),
            "retry_budget_exhausted": retry_budget_exhausted,
            "failure_receipt": commit.receipt.model_dump(mode="json"),
        },
    }
