"""Admit a trace origin on a research request, and show it on thread/list.

A request without an origin passes as it was. An origin is checked against the stored trace (the
client only names the row) and replaced by the facts the server reads. A request that was already
accepted (a resend with the same key) is not checked again, so a resend never fails because the
verdict moved on. The check runs inside the research handlers, after their own replay check.
"""

from __future__ import annotations

import functools
from typing import Protocol, cast

from pydantic import JsonValue, ValidationError

from thoth.application.commands.verification_trace import require_trace_project
from thoth.application.services.request_records import RequestRecords
from thoth.application.services.trace_origin import TraceOriginRefused, build_trace_origin
from thoth.application.services.verification_trace import VerificationTraceService
from thoth.domain.research_request import ResearchAttempt
from thoth.domain.trace_origin import TraceOriginInput
from thoth.ports.operation import OperationStorePort
from thoth.ports.project import ProjectStorePort
from thoth.protocol.deferred import (
    AcceptedRunning,
    EphemeralCommandResult,
    PendingExecution,
    current_operation,
)
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode
from thoth.protocol.registry import CommandHandler

_RESEARCH_METHODS = frozenset({"thread/start", "thread/input"})
Origin = dict[str, JsonValue]
HandlerResult = dict[str, JsonValue] | AcceptedRunning | PendingExecution | EphemeralCommandResult


class OriginHost(Protocol):
    records: RequestRecords
    operations: OperationStorePort
    projects: ProjectStorePort


def thread_origins(
    records: RequestRecords, operations: OperationStorePort, project_id: str
) -> dict[str, Origin]:
    """The row each thread belongs to: the newest origin among its accepted research requests."""
    found: dict[str, Origin] = {}
    for operation in operations.list_by_project(project_id):
        if operation.method not in _RESEARCH_METHODS:
            continue
        attempt = records.journal_read(project_id, operation.operation_id, ResearchAttempt)
        origin = None if attempt is None else attempt.continuation.get("origin")
        thread_id = None if attempt is None else attempt.continuation.get("thread_id")
        if isinstance(origin, dict) and isinstance(thread_id, str):
            row = cast(dict[str, JsonValue], origin)
            found[thread_id] = {
                "subject_kind": row.get("subject_kind"),
                "subject_id": row.get("subject_id"),
                "verdict_revision": row.get("verdict_revision"),
            }
    return found


def admit_trace_origin(
    host: OriginHost, method: str, value: dict[str, JsonValue]
) -> dict[str, JsonValue]:
    """The request with its origin replaced by the server's own, or as it was without one."""
    if "origin" not in value:
        return value
    if value["origin"] is None:
        return {key: item for key, item in value.items() if key != "origin"}
    if method == "thread/steer":
        raise _refused("TRACE_ORIGIN_NOT_ALLOWED")
    project_id = str(value.get("project_id", ""))
    if _already_accepted(host.records, project_id):
        return value
    try:
        claimed = TraceOriginInput.model_validate(value["origin"])
    except ValidationError as exc:
        raise _refused("TRACE_ORIGIN_INVALID", RpcErrorCode.INVALID_PARAMS) from exc
    require_trace_project(host.projects, project_id)
    try:
        origin = build_trace_origin(VerificationTraceService(host.records), project_id, claimed)
    except TraceOriginRefused as exc:
        raise _refused(str(exc)) from exc
    if method == "thread/input":
        own = thread_origins(host.records, host.operations, project_id).get(
            str(value.get("thread_id"))
        )
        if own is None or (own["subject_kind"], own["subject_id"]) != (
            origin.subject_kind,
            origin.subject_id,
        ):
            raise _refused("TRACE_ORIGIN_THREAD_MISMATCH")
    return {**value, "origin": origin.model_dump(mode="json")}


def with_thread_origins(
    inner: CommandHandler, records: RequestRecords, operations: OperationStorePort
) -> CommandHandler:
    """thread/list with each thread's origin (null for an ordinary or older thread)."""

    @functools.wraps(inner)
    async def listed(value: dict[str, JsonValue]) -> HandlerResult:
        result = await inner(value)
        if not isinstance(result, dict) or not isinstance(result.get("threads"), list):
            return result
        found = thread_origins(records, operations, str(value["project_id"]))
        shown: list[JsonValue] = []
        for item in cast(list[JsonValue], result["threads"]):
            if isinstance(item, dict):
                shown.append({**item, "origin": found.get(str(item.get("thread_id")))})
            else:
                shown.append(item)
        return {**result, "threads": shown}

    return listed


def _already_accepted(records: RequestRecords, project_id: str) -> bool:
    operation = current_operation.get()
    if operation is None:
        return False
    key = operation.operation_id
    if records.journal_read(project_id, key, ResearchAttempt) is not None:
        return True
    return records.controls.read(project_id, "RESEARCH_EXECUTION", f"queue:{key}") is not None


def _refused(reason: str, code: RpcErrorCode = RpcErrorCode.DOMAIN_REJECTED) -> RpcApplicationError:
    return RpcApplicationError(code, reason)
