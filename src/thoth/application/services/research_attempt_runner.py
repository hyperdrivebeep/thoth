"""Execute one durable research attempt; commands own admission and presentation."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, cast

from pydantic import JsonValue

from thoth.application.services.post_execution_resume import resume_post_execution_learning
from thoth.application.services.research_boundary import RequestBoundary
from thoth.application.services.research_termination import ResearchTerminationService
from thoth.domain.enums import EntityType, OperationState, ThreadExecutionState
from thoth.domain.operation import OperationRecord
from thoth.domain.research_execution import ResearchFence, ResearchWork, research_work
from thoth.domain.research_failure import ResearchPublicationError, failure_cause
from thoth.domain.research_lease import ResearchLease, ResearchLeaseLost, ResearchPaused
from thoth.domain.research_request import InputDelivery, ResearchAttempt, ThreadRequestRevision
from thoth.ports.model import ModelExecutionHold
from thoth.protocol.deferred import AcceptedRunning, PendingExecution
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode

if TYPE_CHECKING:
    from thoth.application.commands.research_threads import ResearchThreadHandlers


async def run_research_attempt(
    host: ResearchThreadHandlers,
    attempt: ResearchAttempt,
    request: ThreadRequestRevision,
    operation: OperationRecord,
) -> dict[str, JsonValue] | PendingExecution:
    while True:
        current = host.operations.read(operation.operation_id)
        if current is None:
            return PendingExecution({"status": "OPERATION_MISSING"})
        if current.state != OperationState.RUNNING:
            return (
                current.result
                if current.result is not None
                else PendingExecution({"status": current.state.value})
            )
        if attempt.external_effect_state != "NONE":
            if attempt.external_effect_state == "RETURNED":
                recovered = await resume_post_execution_learning(host, attempt, current)
                if recovered is not None:
                    terminal = host.operations.read(current.operation_id)
                    if terminal is not None and terminal.state == OperationState.SUCCEEDED:
                        return terminal.result or {}
                    return PendingExecution({"status": recovered.reason_code or recovered.state})
            return host.pending(attempt, "EXTERNAL_EFFECT_RECONCILIATION_REQUIRED")
        current_request = host.records.read(
            request.project_id, EntityType.THREAD, f"request:{request.thread_id}"
        )
        if current_request is None or current_request[0] != attempt.request_ref:
            raise ResearchFence("REQUEST_SUPERSEDED")
        thread = host.threads.read(request.thread_id)
        if thread is not None and thread.execution_state in {
            ThreadExecutionState.PAUSED,
            ThreadExecutionState.PAUSE_PENDING,
        }:
            return host.pending(attempt, "PAUSED")
        lease = host.leases.claim(attempt)
        if lease is not None:
            break
        await asyncio.sleep(0.05)
    try:
        attempt = attempt.model_copy(
            update={"attempt_epoch": lease.epoch, "worker_id": lease.worker_id, "status": "RUNNING"}
        )
        host.records.journal(request.project_id, attempt.operation_id, attempt)
        thread = host.threads.read(request.thread_id)
        if thread is not None and thread.execution_state == ThreadExecutionState.RUNNING:
            # Recovered v2 owners may replace stale views; legacy liveness remains unknown.
            if lease.epoch == 1:
                return host.pending(attempt, "WAITING_FOR_LEGACY_THREAD")
            host.threads.update(
                thread.model_copy(
                    update={
                        "execution_state": ThreadExecutionState.IDLE,
                        "revision": thread.revision + 1,
                    }
                ),
                expected_revision=thread.revision,
            )
        return await _execute_owned(host, attempt, request, operation, lease)
    finally:
        try:
            host.leases.release(lease)
        except Exception as cleanup:
            # Lease cleanup is separate evidence; never replace a sealed primary failure.
            from thoth.domain.research_failure import ResearchCleanupFailure

            try:
                key = f"failure-cleanup:{attempt.operation_id}:{attempt.attempt_epoch}"
                with host.records.ledger.transaction():
                    if (
                        host.records.journal_read(request.project_id, key, ResearchCleanupFailure)
                        is None
                    ):
                        host.records.journal(
                            request.project_id,
                            key,
                            ResearchCleanupFailure(
                                operation_id=attempt.operation_id,
                                attempt_epoch=attempt.attempt_epoch,
                                cause=failure_cause(cleanup, "LEASE_RELEASE"),
                                created_at=host.records.clock.now(),
                            ),
                            "FAILED",
                        )
            except Exception as recording:
                import logging

                logging.getLogger(__name__).error(
                    "Research cleanup diagnostic was not stored: %s / %s",
                    type(cleanup).__name__,
                    type(recording).__name__,
                )


async def _execute_owned(
    host: ResearchThreadHandlers,
    attempt: ResearchAttempt,
    request: ThreadRequestRevision,
    operation: OperationRecord,
    lease: ResearchLease,
) -> dict[str, JsonValue] | PendingExecution:
    boundary, work = _prepare_work(host, attempt, request, operation, lease)
    token = research_work.set(work)

    def publish_result(
        phase: str, result: dict[str, object], gaps: tuple[str, ...], terminal: str | None = None
    ) -> None:
        try:
            host.publish(attempt, request, work, phase, result, gaps, terminal=terminal)
        except (ResearchFence, ResearchLeaseLost, ResearchPaused, ModelExecutionHold):
            raise
        except Exception as exc:
            raise ResearchPublicationError(exc) from exc

    async def publish(phase: str, result: dict[str, object], gaps: tuple[str, ...]) -> None:
        publish_result(phase, result, gaps)

    def terminate(
        primary_error: Exception, origin: str, secondary_error: Exception | None = None
    ) -> PendingExecution:
        from thoth.domain.research_failure import FailureOrigin

        primary = failure_cause(primary_error, cast(FailureOrigin, origin))
        secondary = (
            None if secondary_error is None else failure_cause(secondary_error, "HOLD_PUBLICATION")
        )
        logging.getLogger(__name__).error(
            "research attempt failed origin=%s code=%s detail=%s",
            origin,
            primary.reason_code,
            primary.detail,
            exc_info=primary_error,
        )
        try:
            ResearchTerminationService(host.records, host.operations, host.threads).finish_failure(
                attempt, request, primary, secondary
            )
        except ResearchLeaseLost:
            return PendingExecution({"status": "ATTEMPT_FENCED"})
        except Exception as recording:
            raise RpcApplicationError(
                RpcErrorCode.INTERNAL_ERROR,
                "research failure recording failed",
                data={
                    "primary": primary.model_dump(mode="json"),
                    "secondary": None if secondary is None else secondary.model_dump(mode="json"),
                    "recording_failure": failure_cause(recording, "FAILURE_RECORDING").model_dump(
                        mode="json"
                    ),
                },
            ) from recording
        return PendingExecution({"status": "FAILED"})

    try:
        boundary.check()
        project, thread = (
            host.projects.read(request.project_id),
            host.threads.read(request.thread_id),
        )
        assert project is not None and thread is not None
        model_name = attempt.continuation.get("model")
        model = host.models.resolve(
            provider=str(attempt.continuation.get("provider", "default")),
            model=model_name if isinstance(model_name, str) else None,
        )
        await publish(
            "CONNECTED_SOURCES", {"message": "Examining authorized connected sources"}, ()
        )

        async def prepare() -> None:
            analysis = await host.analysis.run(work, project, thread, model, publish)
            await publish("HYPOTHESES", analysis, ())

        work.prepare = prepare
        legacy_input = {
            k: v
            for k, v in attempt.continuation.items()
            if k
            in {
                "project_id",
                "thread_id",
                "provider",
                "model",
                "reference_request",
                "reference_answer",
            }
        }
        legacy_input["instruction"] = request.authored_text
        result = await host.analyze_legacy(cast(dict[str, JsonValue], legacy_input))
        if isinstance(result, (AcceptedRunning, PendingExecution)):
            raise ValueError("NESTED_RESEARCH_ADMISSION_FORBIDDEN")
        boundary.check()
        if result.get("queued") is True:
            return host.pending(attempt, "WAITING_FOR_LEGACY_THREAD")
        combined = {
            **work.context,
            **result,
            "cleanup_usage": [
                usage.model_dump(mode="json") for usage in work.cleanup_usage.values()
            ],
        }
        publish_result(
            "COMPLETE",
            combined,
            (),
            terminal="BOUNDED_RESEARCH_COMPLETE",
        )
        return cast(
            dict[str, JsonValue],
            {
                "contract_version": 2,
                "thread_id": request.thread_id,
                "request_epoch": request.request_epoch,
                "terminal_reason": "BOUNDED_RESEARCH_COMPLETE",
                **combined,
            },
        )
    except ResearchLeaseLost:
        return PendingExecution({"status": "ATTEMPT_FENCED"})
    except ResearchPaused:
        return host.pending(attempt, "PAUSED")
    except ModelExecutionHold as exc:
        # Preserve the observed primary before attempting a partial checkpoint.
        reason = failure_cause(exc, "MODEL_CALL").reason_code
        try:
            host.publish(
                attempt,
                request,
                work,
                "HOLD",
                work.context,
                (reason,),
                terminal=reason,
                check=False,
            )
        except (ResearchFence, ResearchLeaseLost, ResearchPaused):
            return PendingExecution({"status": "ATTEMPT_FENCED"})
        except Exception as publication:
            return terminate(exc, "MODEL_CALL", publication)
        return {
            "contract_version": 2,
            "thread_id": request.thread_id,
            "terminal_reason": reason,
            "research_state": "PARTIAL",
        }
    except (ResearchFence, asyncio.CancelledError):
        if not boundary.owns_attempt():
            return PendingExecution({"status": "ATTEMPT_FENCED"})
        current_operation = host.operations.read(operation.operation_id)
        current_thread = host.threads.read(request.thread_id)
        task = asyncio.current_task()
        if (
            task is not None
            and task.cancelling()
            and current_operation is not None
            and current_operation.state == OperationState.RUNNING
            and current_thread is not None
            and current_thread.lifecycle.value == "OPEN"
        ):
            return host.pending(attempt, "RECOVERY_REQUIRED")
        with host.records.ledger.transaction():
            current = host.records.read(
                request.project_id, EntityType.THREAD, f"request:{request.thread_id}"
            )
            if current is not None and current[0] == attempt.request_ref:
                for identifier in request.accepted_input_ids:
                    delivery = host.records.journal_read(
                        request.project_id, f"input:{identifier}", InputDelivery
                    )
                    if delivery is not None and delivery.state == "ACCEPTED":
                        host.records.journal(
                            request.project_id,
                            f"input:{identifier}",
                            delivery.model_copy(update={"state": "CANCELLED"}),
                            "CANCELLED",
                        )
            host.records.journal(
                request.project_id,
                attempt.operation_id,
                attempt.model_copy(update={"status": "CANCELLED", "phase": "FENCED"}),
                "CANCELLED",
            )
        raise
    except Exception as exc:
        if isinstance(exc, ResearchPublicationError):
            return terminate(exc.original, "RESULT_PUBLICATION")
        return terminate(exc, "RESEARCH_EXECUTION")
    finally:
        research_work.reset(token)


def _prepare_work(
    host: ResearchThreadHandlers,
    attempt: ResearchAttempt,
    request: ThreadRequestRevision,
    operation: OperationRecord,
    lease: ResearchLease,
) -> tuple[RequestBoundary, ResearchWork]:
    boundary = RequestBoundary(
        host.records,
        host.projects,
        host.threads,
        host.governance,
        host.operations,
        operation,
        request,
    )
    boundary.validate_owner = lambda: host.leases.validate(lease)
    work = ResearchWork(attempt.request_ref, request.effective_question, boundary)
    from thoth.application.services.research_basis_capture import capture_rendered_memory

    work.observe_context = lambda context: capture_rendered_memory(context, host.records.ledger)
    work.model_settings = request.model_settings
    if request.model_settings is not None:
        work.context["model_settings"] = request.model_settings.model_dump(mode="json")
    if attempt.continuation.get("retry_policy") == "ONCE_TRANSIENT_429":
        from thoth.domain.oauth_retry import OAuthRetryPolicy, allows_once_transient_429

        settings = request.model_settings
        if settings is not None and allows_once_transient_429(
            provider=settings.provider,
            capability_source=settings.capability_source,
        ):
            work.oauth_retry_policy = OAuthRetryPolicy()
    boundary.validate_sources = lambda: host.analysis.require_current_sources(
        work.evidence, work.context.get("source_context_digest")
    )

    def show_draft(phase: str, content: dict[str, object]) -> None:
        boundary.check()
        with host.records.ledger.transaction():
            latest = (
                host.records.journal_read(request.project_id, attempt.operation_id, ResearchAttempt)
                or attempt
            )
            previous = latest.draft_progress
            from thoth.application.services.research_progress_view import merge_draft_progress

            host.records.journal(
                request.project_id,
                attempt.operation_id,
                latest.model_copy(
                    update={
                        "phase": phase,
                        "draft_progress": merge_draft_progress(previous, content),
                    }
                ),
            )

    work.show_draft = show_draft

    def observe_external_effect(effect_ref: str, state: str) -> None:
        boundary.check()
        with host.records.ledger.transaction():
            latest = (
                host.records.journal_read(request.project_id, attempt.operation_id, ResearchAttempt)
                or attempt
            )
            host.records.journal(
                request.project_id,
                attempt.operation_id,
                latest.model_copy(
                    update={"external_effect_state": state, "external_effect_ref": effect_ref}
                ),
            )

    work.observe_external_effect = observe_external_effect
    return boundary, work
