"""Present and persist nonterminal research-attempt admission state."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from pydantic import JsonValue

from thoth.domain.enums import ThreadExecutionState
from thoth.domain.research_request import ResearchAttempt
from thoth.protocol.deferred import PendingExecution

if TYPE_CHECKING:
    from thoth.application.commands.research_threads import ResearchThreadHandlers


def accepted_attempt_value(attempt: ResearchAttempt) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        {
            "contract_version": 2,
            "status": "PAUSED" if attempt.status == "PAUSED" else "ACCEPTED_RUNNING",
            "project_id": attempt.request_ref.project_id,
            "thread_id": attempt.continuation["thread_id"],
            "operation_id": attempt.operation_id,
            "request_ref": attempt.request_ref.model_dump(mode="json"),
            "request_epoch": attempt.continuation["request_epoch"],
            "input_id": attempt.continuation["input_id"],
            "phase": attempt.phase,
            "input_state": "ACCEPTED",
            "recovery": "CHECKPOINT_READABLE_RESUBMIT_TO_CONTINUE",
        },
    )


def pending_attempt(
    host: ResearchThreadHandlers, attempt: ResearchAttempt, status: str
) -> PendingExecution:
    with host.records.ledger.transaction():
        current = (
            host.records.journal_read(
                attempt.request_ref.project_id, attempt.operation_id, ResearchAttempt
            )
            or attempt
        )
        if (
            current.worker_id not in {None, attempt.worker_id}
            or current.attempt_epoch > attempt.attempt_epoch
        ):
            return PendingExecution({"status": "ATTEMPT_FENCED"})
        if status == "PAUSED":
            thread = host.threads.read(str(attempt.continuation["thread_id"]))
            if thread is not None and thread.execution_state.value == "PAUSE_PENDING":
                host.threads.update(
                    thread.model_copy(
                        update={
                            "execution_state": ThreadExecutionState.PAUSED,
                            "revision": thread.revision + 1,
                        }
                    ),
                    expected_revision=thread.revision,
                )
        current = current.model_copy(update={"status": status, "phase": status})
        host.records.journal(
            attempt.request_ref.project_id, attempt.operation_id, current, status
        )
    return PendingExecution({**accepted_attempt_value(current), "status": status})
