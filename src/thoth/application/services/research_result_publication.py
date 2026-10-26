"""Publish one research checkpoint in its existing transaction and ownership boundaries."""

from collections.abc import Callable
from typing import cast

from pydantic import JsonValue

from thoth.application.services.request_language import with_result_language
from thoth.application.services.request_records import RequestRecords
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import EntityType
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.research_execution import ResearchFence, ResearchWork
from thoth.domain.research_request import (
    InputDelivery,
    ResearchAttempt,
    RevisionRef,
    ThreadRequestRevision,
)
from thoth.domain.resource_scope import current_resource_uses
from thoth.ports.operation import OperationStorePort
from thoth.ports.resource_scope import ResourceAccessPort


def publish_research_result(
    attempt: ResearchAttempt,
    request: ThreadRequestRevision,
    work: ResearchWork,
    phase: str,
    result: dict[str, object],
    gaps: tuple[str, ...],
    terminal: str | None = None,
    check: bool = True,
    *,
    records: RequestRecords,
    operations: OperationStorePort,
    access: ResourceAccessPort,
    source_digest: Callable[[tuple[EvidenceSpan, ...]], str],
) -> RevisionRef:
    with records.ledger.transaction():
        if check:
            work.boundary.check()
        if not work.boundary.owns_attempt():
            from thoth.domain.research_lease import ResearchLeaseLost

            raise ResearchLeaseLost("ATTEMPT_LEASE_FENCED")
        # Recheck request even on budget/transport HOLD; stale work cannot become current.
        head = records.read(request.project_id, EntityType.THREAD, f"request:{request.thread_id}")
        if head is None or head[0] != attempt.request_ref:
            raise ResearchFence("STALE_PUBLICATION")
        uses = current_resource_uses() or ()
        operation = operations.read(attempt.operation_id)
        if operation is None:
            raise ResearchFence("OPERATION_MISSING")
        access.require_operation(operation.model_copy(update={"resource_uses": uses}))
        if isinstance(result.get("portfolio"), dict):
            from thoth.application.services.hypothesis_grounding_bindings import (
                require_grounding_publication,
            )

            require_grounding_publication(work)
        staged = tuple(work.pending_revisions)
        records.commit_staged(staged)
        from thoth.application.services.research_basis_capture import capture_record_producers

        capture_record_producers(work, records.ledger)
        from thoth.application.services.research_basis_capture import result_basis
        from thoth.domain.research_request import CurrentResultManifestV21

        manifest = CurrentResultManifestV21(
            research_basis=result_basis(work, request, records.ledger),
            request_ref=attempt.request_ref,
            operation_id=attempt.operation_id,
            attempt_epoch=attempt.attempt_epoch,
            basis_digest=domain_digest(
                "RESEARCH_BASIS",
                "2.0.0",
                canonical_payload(
                    {
                        "request": attempt.request_ref,
                        "refs": work.record_refs,
                        "evidence": work.evidence,
                        "uses": uses,
                    }
                ),
            ),
            phase=phase,
            completion="TERMINAL" if terminal else "CHECKPOINT",
            input_ids=request.accepted_input_ids,
            record_refs=tuple(work.record_refs),
            source_refs=tuple(s.span_id for s in work.evidence),
            resource_uses=tuple(u.model_dump(mode="python") for u in uses),
            source_context_digest=source_digest(work.evidence),
            source_context_version="2.2.0",
            source_basis={
                s.span_id: f"{s.source_version_id}:{s.text_sha256}" for s in work.evidence
            },
            result=with_result_language(result, request),
            gaps=gaps,
            next_steps=gaps,
            terminal_reason=terminal,
        )
        ref = records.save(
            request.project_id,
            EntityType.DECISION_OBJECT,
            f"result:{request.thread_id}",
            manifest,
            evidence_refs=manifest.source_refs,
        )
        records.journal(
            request.project_id,
            attempt.operation_id,
            (
                records.journal_read(request.project_id, attempt.operation_id, ResearchAttempt)
                or attempt
            ).model_copy(
                update={
                    "phase": phase,
                    "checkpoint_ref": ref,
                    "status": "SUCCEEDED" if terminal else "RUNNING",
                }
            ),
        )
        # Input is APPLIED only after an analysis result, not its acceptance checkpoint.
        if phase != "CONNECTED_SOURCES" and "requirements" in work.context:
            for input_id in request.accepted_input_ids:
                delivery = records.journal_read(
                    request.project_id, f"input:{input_id}", InputDelivery
                )
                if delivery is not None and delivery.first_result_ref is None:
                    records.journal(
                        request.project_id,
                        f"input:{input_id}",
                        delivery.model_copy(update={"state": "APPLIED", "first_result_ref": ref}),
                        "APPLIED",
                    )
        event_cursor = None
        if records.events is not None:
            records.events.checkpoint(
                operation_id=attempt.operation_id,
                payload={
                    "result_ref": ref.model_dump(mode="json"),
                    "phase": phase,
                    "request_epoch": request.request_epoch,
                    "completion": manifest.completion,
                },
            )
            event = records.events.append(
                project_id=request.project_id,
                operation_id=attempt.operation_id,
                event_type="research.terminal" if terminal else "research.progress",
                payload={
                    "result_ref": ref.model_dump(mode="json"),
                    "phase": phase,
                    "terminal_reason": terminal,
                },
            )
            event_cursor = event.event_id
        if event_cursor is not None:
            latest = (
                records.journal_read(request.project_id, attempt.operation_id, ResearchAttempt)
                or attempt
            )
            records.journal(
                request.project_id,
                attempt.operation_id,
                latest.model_copy(update={"event_cursor": event_cursor}),
            )
        if terminal is not None:
            operations.complete(
                attempt.operation_id,
                cast(
                    dict[str, JsonValue],
                    {
                        **result,
                        "contract_version": 2,
                        "thread_id": request.thread_id,
                        "request_epoch": request.request_epoch,
                        "terminal_reason": terminal,
                    },
                ),
                completed_at=records.clock.now(),
            )
    del work.pending_revisions[: len(staged)]
    return ref
