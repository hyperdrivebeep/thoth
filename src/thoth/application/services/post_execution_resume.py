"""Existing thread/resume can resume learning only after a committed external outcome."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from thoth.application.services.committed_learning_recovery import (
    finish_learning_recovery,
    require_committed_learning,
)
from thoth.application.services.post_execution_memory import PostExecutionMemory
from thoth.application.services.research_boundary import RequestBoundary
from thoth.domain.enums import EntityType, OperationState, ThreadExecutionState
from thoth.domain.operation import OperationRecord
from thoth.domain.post_execution_learning import PostExecutionLearningResult
from thoth.domain.research_execution import ResearchFence, ResearchWork, research_work
from thoth.domain.research_lease import ResearchLeaseLost, ResearchPaused
from thoth.domain.research_request import ResearchAttempt, ThreadRequestRevision

if TYPE_CHECKING:
    from thoth.application.commands.research_threads import ResearchThreadHandlers


async def resume_post_execution_learning(
    host: ResearchThreadHandlers,
    attempt: ResearchAttempt,
    operation: OperationRecord,
    *,
    resume_pause: bool = False,
) -> PostExecutionLearningResult | None:
    host.operation_access.require_execution_owner(operation)
    if attempt.external_effect_state != "RETURNED":
        return None
    thread_id = str(attempt.continuation["thread_id"])
    rows = [
        host.records.read(operation.project_id, EntityType.THREAD, key.removeprefix("THREAD:"))
        for key in host.records.ledger.read_heads(operation.project_id)
        if key.startswith(f"THREAD:learning:{thread_id}:")
    ]
    results = [PostExecutionLearningResult.model_validate(row[1]) for row in rows if row]
    result = next(
        (
            r
            for r in results
            if r.basis.request_ref == attempt.request_ref
            and r.state in {"HELD", "PENDING", "COMMITTED"}
        ),
        None,
    )
    if result is None:
        return None
    try:
        require_committed_learning(host, attempt, result)
    except ValueError:
        return None
    saved = host.records.read(operation.project_id, EntityType.THREAD, f"request:{thread_id}")
    project, thread = host.projects.read(operation.project_id), host.threads.read(thread_id)
    if saved is None or saved[0] != attempt.request_ref or project is None or thread is None:
        raise ValueError("POST_EXECUTION_REQUEST_STALE")
    paused_states = {ThreadExecutionState.PAUSED, ThreadExecutionState.PAUSE_PENDING}
    if not resume_pause and thread.execution_state in paused_states:
        return result.model_copy(update={"state": "PENDING", "reason_code": "PAUSED"})
    request = ThreadRequestRevision.model_validate(saved[1])
    boundary = RequestBoundary(
        host.records,
        host.projects,
        host.threads,
        host.governance,
        host.operations,
        operation,
        request,
        post_effect_learning=True,
    )
    work = ResearchWork(attempt.request_ref, request.effective_question, boundary)
    source = {s.span_id: s for s in host.analysis.evidence(operation.project_id)}
    work.evidence = tuple(source[key] for key in result.basis.source_refs if key in source)
    work.model_settings = request.model_settings
    boundary.validate_sources = lambda: host.analysis.require_current_sources(work.evidence, None)
    lease = host.leases.claim(attempt)
    if lease is None:
        return result.model_copy(
            update={"state": "PENDING", "reason_code": "LEARNING_ALREADY_RUNNING"}
        )
    boundary.validate_owner = lambda: host.leases.validate(lease)
    token = research_work.set(work)
    try:
        with host.records.ledger.transaction():
            host.operation_access.require_execution_owner(operation)
            host.leases.validate(lease)
            require_committed_learning(host, attempt, result)
            thread = host.threads.read(thread_id)
            if thread is None:
                raise ResearchFence("THREAD_MISSING")
            if resume_pause and thread.execution_state in paused_states:
                resumed = thread.model_copy(
                    update={
                        "execution_state": ThreadExecutionState.RUNNING,
                        "revision": thread.revision + 1,
                    }
                )
                if not host.threads.update(resumed, expected_revision=thread.revision):
                    raise ResearchFence("THREAD_REVISION_CHANGED")
                thread = resumed
            # Any stale authority/request/source rolls back the pause transition too.
            boundary.check()
            attempt = attempt.model_copy(
                update={"attempt_epoch": lease.epoch, "worker_id": lease.worker_id}
            )
            if operation.state == OperationState.RUNNING:
                host.records.journal(operation.project_id, attempt.operation_id, attempt)
        learned = await PostExecutionMemory(host.records, host.analysis.memory).learn(
            project, thread, work, result.basis.execution
        )
        if operation.state == OperationState.RUNNING and learned.state != "STALE":
            finish_learning_recovery(host, attempt, request, work, learned)
        return learned
    except ResearchPaused:
        return result.model_copy(update={"state": "PENDING", "reason_code": "PAUSED"})
    except (ResearchFence, ResearchLeaseLost, asyncio.CancelledError) as exc:
        return result.model_copy(
            update={"state": "PENDING", "reason_code": type(exc).__name__ + ":" + str(exc)[:160]}
        )
    finally:
        research_work.reset(token)
        host.leases.release(lease)
