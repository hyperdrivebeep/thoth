"""Bound each connector await and retain an observation even for cleanup failure."""

import asyncio
from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Literal

from thoth.application.services.control_record_service import ControlRecordService
from thoth.domain.base import DomainModel
from thoth.domain.canonical import canonical_payload
from thoth.domain.connectors import ConnectorAccessRequest, ConnectorErrorCode, ConnectorFailure
from thoth.domain.research_execution import research_work

_detached: set[asyncio.Task[object]] = set()


class ConnectorIoObservation(DomainModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    connector_id: str
    phase: str
    state: str
    elapsed_ms: int
    timeout_ms: int
    remote_stop: Literal["UNKNOWN"] = "UNKNOWN"


def retain_detached(task: asyncio.Task[object]) -> None:
    _detached.add(task)

    def done(finished: asyncio.Task[object]) -> None:
        _detached.discard(finished)
        if not finished.cancelled():
            finished.exception()

    task.add_done_callback(done)


async def connector_io[T](
    request: ConnectorAccessRequest,
    phase: Literal["DISCOVER", "FETCH"],
    action: Callable[[], Awaitable[T]],
    records: ControlRecordService,
) -> T:
    work = research_work.get()
    if phase not in {"DISCOVER", "FETCH"}:
        raise ValueError("RESEARCH_IO_PHASE_INVALID")
    timeout = 120.0
    if work is not None:
        work.boundary.reserve(len(canonical_payload(request)))
        remaining = work.boundary.call_timeout()
        if remaining is not None:
            timeout = min(timeout, remaining)
    started, state = monotonic(), "FAILED"

    async def invoke() -> T:
        return await action()

    task = asyncio.create_task(invoke())
    try:
        done, _ = await asyncio.wait({task}, timeout=timeout)
        if not done:
            task.cancel()
            state = "TIMEOUT_LOCAL_CANCEL_REQUESTED"
            raise ConnectorFailure(
                ConnectorErrorCode.DRIVER_ERROR, f"CONNECTOR_{phase}_TIMEOUT_REMOTE_STOP_UNKNOWN"
            )
        result = task.result()
        state = "RETURNED"
        return result
    except asyncio.CancelledError:
        task.cancel()
        state = "LOCAL_CANCEL_REQUESTED"
        raise
    finally:
        if not task.done():
            # Do not let an uncooperative driver make the user's timeout unbounded.
            from typing import cast

            retain_detached(cast(asyncio.Task[object], task))
        observation = ConnectorIoObservation(
            connector_id=request.connector_id,
            phase=phase,
            state=state,
            elapsed_ms=round((monotonic() - started) * 1000),
            timeout_ms=round(timeout * 1000),
        )
        records.create(
            project_id=request.project_id,
            namespace="CONNECTOR",
            record_type="IO",
            state=state,
            payload=observation.model_dump(mode="json"),
        )
