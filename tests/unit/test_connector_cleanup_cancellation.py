"""Caller cancellation is distinct from a close task cancelling itself."""

import asyncio
from pathlib import Path

import pytest
from tests.integration.test_research_memory_basis import Boundary
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.storage.control_record import SqliteControlRecordStore
from thoth.application.services.connector_cleanup import connector_cleanup
from thoth.application.services.control_record_service import ControlRecordService
from thoth.domain.cleanup import CleanupUsage
from thoth.domain.connectors import ConnectorAccessRequest
from thoth.domain.control_record import ControlRecord
from thoth.domain.research_execution import ResearchWork, research_work
from thoth.domain.research_request import RevisionRef


def command() -> ConnectorAccessRequest:
    return ConnectorAccessRequest(
        project_id="p",
        connector_id="fixture",
        actor_id="human:local-user",
        selector={"path": "data"},
        policy_id="p",
        policy_revision=1,
        policy_digest="a" * 64,
    )


@pytest.mark.parametrize("write_fault", (False, True))
async def test_caller_cancel_is_rethrown_after_usage_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, write_fault: bool
) -> None:
    runtime = await setup(tmp_path, ControlledResearchModel(), source=False)
    records = ControlRecordService(
        store=SqliteControlRecordStore(runtime.ledger.engine),
        clock=SystemClock(),
        ids=UuidIdGenerator(),
    )
    work = ResearchWork(
        RevisionRef(
            project_id="p",
            entity_type="THREAD",
            entity_id="r",
            revision_id="r",
            revision_digest="a" * 64,
        ),
        "fixture",
        Boundary(),
    )
    token = research_work.set(work)
    begun = asyncio.Event()
    cancelled = asyncio.Event()
    calls: list[str] = []
    trace: list[str] = []
    caught: list[asyncio.CancelledError] = []
    original_wait = asyncio.wait
    original_create = records.create

    class UsageTrace(dict[str, CleanupUsage]):
        def __setitem__(self, key: str, usage: CleanupUsage) -> None:
            trace.append("memory:" + usage.state)
            super().__setitem__(key, usage)

    work.cleanup_usage = UsageTrace()

    async def observed_wait(
        tasks: set[asyncio.Task[object]], *, timeout: float
    ) -> tuple[set[asyncio.Task[object]], set[asyncio.Task[object]]]:
        try:
            return await original_wait(tasks, timeout=timeout)
        except asyncio.CancelledError as error:
            caught.append(error)
            trace.append("caught")
            raise

    async def close() -> None:
        calls.append("close")
        begun.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    def observe(
        *,
        project_id: str,
        namespace: str,
        record_type: str,
        state: str,
        payload: dict[str, object],
        record_id: str | None = None,
    ) -> ControlRecord:
        trace.append("record:" + state)
        if write_fault:
            raise RuntimeError("fixture observation write failed")
        return original_create(
            project_id=project_id,
            namespace=namespace,
            record_type=record_type,
            state=state,
            payload=payload,
            record_id=record_id,
        )

    monkeypatch.setattr(records, "create", observe)
    monkeypatch.setattr(asyncio, "wait", observed_wait)
    try:
        task = asyncio.create_task(connector_cleanup(command(), "caller", close, records))
        await asyncio.wait_for(begun.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError) as raised:
            await task
        trace.append("raised")
        assert caught == [raised.value] and caught[0] is raised.value
        assert trace == [
            "record:PENDING",
            "memory:PENDING",
            "caught",
            "record:CANCEL_REQUESTED",
            "memory:CANCEL_REQUESTED",
            "raised",
        ]
        await asyncio.wait_for(cancelled.wait(), timeout=5)
        observed = work.cleanup_usage["cleanup:local:caller"]
        assert observed.state == "CANCEL_REQUESTED" and observed.remote_stop == "UNKNOWN"
        assert observed.limit_ms == 2000 and observed.observation_persisted is not write_fault
        repeated = await connector_cleanup(command(), "caller", close, records)
        assert repeated == observed and calls == ["close"]
        if not write_fault:
            saved = records.read("p", "CONNECTOR", "cleanup:local:caller")
            assert saved is not None and CleanupUsage.model_validate(saved.payload) == observed
    finally:
        research_work.reset(token)
        runtime.close()


async def test_close_self_cancellation_does_not_cancel_its_caller(tmp_path: Path) -> None:
    runtime = await setup(tmp_path, ControlledResearchModel(), source=False)
    records = ControlRecordService(
        store=SqliteControlRecordStore(runtime.ledger.engine),
        clock=SystemClock(),
        ids=UuidIdGenerator(),
    )
    calls: list[str] = []

    async def close() -> None:
        calls.append("close")
        raise asyncio.CancelledError("close task self cancellation")

    try:
        caller = asyncio.current_task()
        assert caller is not None and caller.cancelling() == 0
        usage = await connector_cleanup(command(), "child", close, records)
        assert usage.state == "CANCEL_REQUESTED" and caller.cancelling() == 0
        assert usage.limit_ms == 2000 and usage.remote_stop == "UNKNOWN"
        repeated = await connector_cleanup(command(), "child", close, records)
        assert repeated == usage and calls == ["close"]
    finally:
        runtime.close()


async def test_ordinary_close_failure_remains_observational(tmp_path: Path) -> None:
    runtime = await setup(tmp_path, ControlledResearchModel(), source=False)
    records = ControlRecordService(
        store=SqliteControlRecordStore(runtime.ledger.engine),
        clock=SystemClock(),
        ids=UuidIdGenerator(),
    )
    calls: list[str] = []

    async def close() -> None:
        calls.append("close")
        raise RuntimeError("fixture close failure")

    try:
        usage = await connector_cleanup(command(), "failed-close", close, records)
        assert usage.state == "FAILED" and usage.remote_stop == "UNKNOWN" and usage.limit_ms == 2000
        assert await connector_cleanup(command(), "failed-close", close, records) == usage
        assert calls == ["close"]
    finally:
        runtime.close()
