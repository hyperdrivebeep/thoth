"""Unsettled sandbox results, and how a person clears one so the same work may run again.

An attempt is unsettled while it is still going, of unknown end, or finished but not admitted. The
R2 duplicate-run guard holds new runs of the same work while one is unsettled. Clearing is the
person's decision to run that work again: it needs the execution revision they read, a reason, and
it keeps the earlier attempt and its result as they are.
"""

from __future__ import annotations

from thoth.application.services.execution_service import ExecutionService
from thoth.application.services.revision_service import CommitResult
from thoth.domain.execution_full import PlanExecutionRecord, StepExecutionAttemptRecord
from thoth.ports.execution import ExecutionStorePort
from thoth.ports.ledger import LedgerPort

# An attempt still going, or of unknown end: its result is not settled.
PENDING_ATTEMPT_STATES = frozenset(
    {"DISPATCHED", "RUNNING", "CANCEL_REQUESTED", "UNKNOWN_COMPLETION"}
)
# Written to the execution's audit when a person clears an unsettled result. The guard lets an
# attempt through only when it carries this record.
PENDING_RESULT_CLEARED_EVENT = "execution/pendingResultCleared"


def is_unsettled(attempt: StepExecutionAttemptRecord) -> bool:
    unadmitted = attempt.observation_completeness == "NOT_ADMITTED"
    return attempt.state in PENDING_ATTEMPT_STATES or unadmitted


class PendingResultClearing:
    def __init__(
        self, *, store: ExecutionStorePort, service: ExecutionService, ledger: LedgerPort
    ) -> None:
        self._store = store
        self._service = service
        self._ledger = ledger

    def clear(
        self,
        current: PlanExecutionRecord,
        *,
        attempt_id: str,
        cause_revision_ref: str,
        impact_refs: tuple[str, ...],
        cleared_by: str,
        reason: str,
        input_difference: dict[str, object] | None,
    ) -> tuple[PlanExecutionRecord, CommitResult]:
        with self._ledger.transaction():
            attempt = next(
                (
                    item
                    for item in self._store.list_attempts(
                        current.project_id, current.plan_execution_id
                    )
                    if item.attempt_id == attempt_id
                ),
                None,
            )
            if attempt is None:
                raise ValueError("pending attempt not found in this execution")
            if not is_unsettled(attempt):
                raise ValueError("attempt has a settled result; there is nothing to clear")
            execution, commit = self._service.invalidate(
                current, cause_revision_ref=cause_revision_ref, impact_refs=impact_refs
            )
            # The earlier attempt and its result stay as they are. This records who cleared it,
            # when (the audit time), why, and what differed in the inputs.
            self._service.audit(
                current.project_id,
                current.plan_execution_id,
                PENDING_RESULT_CLEARED_EVENT,
                {
                    "attempt_id": attempt_id,
                    "cleared_by": cleared_by,
                    "reason": reason,
                    "input_difference": input_difference,
                    "execution_revision_before": current.revision,
                    "attempt_state": attempt.state,
                    "observation_completeness": attempt.observation_completeness,
                },
            )
            return execution, commit
