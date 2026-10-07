"""Continue an interrupted run: the same question, asked again as a new operation."""

from __future__ import annotations

from typing import Protocol

from pydantic import JsonValue

from thoth.application.services.request_records import RequestRecords
from thoth.domain.enums import EntityType, OperationState
from thoth.domain.research_request import ResearchAttempt, ThreadRequestRevision
from thoth.ports.operation import OperationStorePort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


class ResumeHost(Protocol):
    records: RequestRecords
    operations: OperationStorePort


def resume_payload(host: ResumeHost, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """The same question as the interrupted run, asked again as a new operation.

    The user asked to continue the latest, finished run of this thread. Its completed stages
    may then be reused (research_stage_reuse.py); nothing is reused without this request.
    """
    project_id, thread_id = str(value["project_id"]), str(value["thread_id"])
    source_id = str(value["resume_from_operation_id"])
    head = host.records.read(project_id, EntityType.THREAD, f"request:{thread_id}")
    source = host.records.journal_read(project_id, source_id, ResearchAttempt)
    finished = host.operations.read(source_id)
    if head is None or source is None or source.request_ref != head[0]:
        raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "RESUME_SOURCE_NOT_CURRENT")
    if finished is None or finished.project_id != project_id:
        raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "RESUME_SOURCE_NOT_CURRENT")
    if finished.state == OperationState.RUNNING:
        raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "RESUME_SOURCE_STILL_RUNNING")
    current = ThreadRequestRevision.model_validate(head[1])
    if len(current.effective_question) > 20_000:
        raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "RESUME_QUESTION_TOO_LONG")
    return {
        **value,
        "instruction": current.effective_question,
        "edit_kind": "REPLACE",
        "expected_request_epoch": current.request_epoch,
    }
