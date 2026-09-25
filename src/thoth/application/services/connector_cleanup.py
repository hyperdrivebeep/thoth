"""Exactly identified, best-effort cleanup independent from exhausted research allowance."""

import asyncio
from collections.abc import Awaitable, Callable
from time import monotonic

from thoth.application.services.connector_io import retain_detached
from thoth.application.services.control_record_service import ControlRecordService
from thoth.domain.cleanup import CleanupUsage
from thoth.domain.connectors import ConnectorAccessRequest
from thoth.domain.research_execution import research_work
from thoth.ports.control_record import ControlRecordStorePort
from thoth.protocol.deferred import current_operation


def cleanup_summary(
    records: ControlRecordStorePort, project: str, operation: str
) -> dict[str, object]:
    usages = tuple(
        CleanupUsage.model_validate(row.payload)
        for row in records.list(project, "CONNECTOR", "CLEANUP_USAGE")
        if row.payload.get("parent_operation_id") == operation
    )
    return {
        "cleanup_calls": sum(u.state != "PENDING" for u in usages),
        "cleanup_pending_unknown": sum(u.state == "PENDING" for u in usages),
        "cleanup_elapsed_ms": sum(u.elapsed_ms for u in usages),
        "allowance_per_cleanup_ms": 2000,
        "charged_as_research_calls": False,
        "usages": [u.model_dump(mode="json") for u in usages],
    }


async def connector_cleanup(
    request: ConnectorAccessRequest,
    run_id: str,
    action: Callable[[], Awaitable[None]],
    records: ControlRecordService,
) -> CleanupUsage:
    operation = current_operation.get()
    key = f"cleanup:{operation.operation_id if operation else 'local'}:{run_id}"
    work = research_work.get()
    usage = CleanupUsage(
        parent_operation_id=None if operation is None else operation.operation_id,
        connector_run_id=run_id,
        connector_id=request.connector_id,
        cleanup_attempt_id=key,
        elapsed_ms=0,
        state="PENDING",
    )
    try:
        previous = records.read(request.project_id, "CONNECTOR", key)
        if previous is not None:
            return CleanupUsage.model_validate(previous.payload)
        records.create(
            project_id=request.project_id,
            namespace="CONNECTOR",
            record_type="CLEANUP_USAGE",
            state="PENDING",
            payload=usage.model_dump(mode="python"),
            record_id=key,
        )
    except Exception:
        usage = usage.model_copy(update={"observation_persisted": False})
    # Keep an in-attempt fallback when observation persistence itself fails.
    if work is not None:
        existing = work.cleanup_usage.get(key)
        if existing is not None:
            return existing
        work.cleanup_usage[key] = usage
    started = monotonic()

    async def invoke() -> object:
        return await action()

    task = asyncio.create_task(invoke())
    try:
        done, _ = await asyncio.wait({task}, timeout=2.0)
        if done:
            task.result()
            state = "COMPLETED"
        else:
            task.cancel()
            state = "CANCEL_REQUESTED"
    except asyncio.CancelledError:
        task.cancel()
        state = "CANCEL_REQUESTED"
    except Exception:
        state = "FAILED"
    if not task.done():
        retain_detached(task)
    usage = usage.model_copy(
        update={"state": state, "elapsed_ms": round((monotonic() - started) * 1000)}
    )
    try:
        records.create(
            project_id=request.project_id,
            namespace="CONNECTOR",
            record_type="CLEANUP_USAGE",
            state=state,
            payload=usage.model_dump(mode="python"),
            record_id=key,
        )
    except Exception:
        usage = usage.model_copy(update={"observation_persisted": False})
    if work is not None:
        work.cleanup_usage[key] = usage
    return usage
