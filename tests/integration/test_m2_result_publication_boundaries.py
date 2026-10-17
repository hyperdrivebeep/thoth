"""A failed result publication rolls back its whole transaction, not earlier checkpoints."""

import json
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import copy_context
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import BaseModel, JsonValue
from sqlalchemy import select
from tests.atomicity.harness import Snapshot, assert_phase_delta, snapshot
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_focus_basis import research_host
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.adapters.storage.operations import SqliteOperationStore
from thoth.adapters.storage.schema import checkpoints, events, operations, semantic_revisions
from thoth.adapters.storage.transaction import SqliteAtomicUnitOfWork, read_connection
from thoth.application.services.operation_journal import OperationJournal
from thoth.application.services.request_records import RequestRecords
from thoth.apps.runtime import create_runtime
from thoth.domain.enums import EntityType, OperationState, ThreadExecutionState
from thoth.domain.event import EventRecord
from thoth.domain.operation import OperationRecord
from thoth.domain.research_execution import ResearchWork, research_work
from thoth.domain.research_request import CurrentResultManifestV21, InputDelivery, RevisionRef
from thoth.domain.revision import StagedRevision


class PublicationFault(RuntimeError):
    pass


@dataclass
class PublicationFrame:
    before: Snapshot
    work: ResearchWork
    pending: tuple[StagedRevision, ...] = ()
    ref: RevisionRef | None = None
    phase: str | None = None


@pytest.mark.parametrize("point", ("manifest", "applied", "event", "complete"))
async def test_result_publication_is_atomic_and_failure_remains_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, point: str
) -> None:
    runtime = await setup(tmp_path, ControlledResearchModel())
    host = research_host(runtime)
    target_phase = "COMPLETE" if point == "complete" else "REQUIREMENTS"
    frames: list[PublicationFrame] = []
    rolled_back: list[PublicationFrame] = []
    fired: list[str] = []
    transaction = SqliteAtomicUnitOfWork.transaction
    save = RequestRecords.save
    journal = RequestRecords.journal
    append = OperationJournal.append
    complete = SqliteOperationStore.complete

    @contextmanager
    def observed_transaction(unit: SqliteAtomicUnitOfWork) -> Generator[None]:
        work = research_work.get()
        ambient = any(
            v is not None
            for k, v in copy_context().items()
            if k.name == "thoth_sqlite_ambient_transaction"
        )
        if work is None or ambient:
            with transaction(unit):
                yield
            return
        frame = PublicationFrame(snapshot(runtime.ledger.engine), work)
        frames.append(frame)
        try:
            with transaction(unit):
                yield
        except PublicationFault:
            assert frame.ref is not None
            assert_phase_delta(frame.before, snapshot(runtime.ledger.engine))
            assert tuple(frame.work.pending_revisions) == frame.pending
            rolled_back.append(frame)
            raise
        finally:
            assert frames.pop() is frame

    def active() -> PublicationFrame | None:
        return frames[-1] if frames and frames[-1].ref is not None else None

    def fail() -> None:
        frame = active()
        assert frame is not None and frame.ref is not None
        assert frame.phase == target_phase
        ids = {frame.ref.revision_id, *(item.revision.revision_id for item in frame.pending)}
        with read_connection(runtime.ledger.engine) as connection:
            assert (
                set(
                    connection.execute(
                        select(semantic_revisions.c.revision_id).where(
                            semantic_revisions.c.revision_id.in_(ids)
                        )
                    ).scalars()
                )
                == ids
            )
        fired.append(point)
        raise PublicationFault("synthetic result publication fault")

    def observed_save(
        records: RequestRecords,
        project_id: str,
        entity_type: EntityType,
        entity_id: str,
        record: BaseModel,
        actor_id: str = "agent:research",
        *,
        evidence_refs: tuple[str, ...] = (),
    ) -> RevisionRef:
        matched = (
            records is host.records
            and isinstance(record, CurrentResultManifestV21)
            and record.phase == target_phase
        )
        if matched:
            assert frames
            assert isinstance(record, CurrentResultManifestV21)
            frames[-1].phase = record.phase
            frames[-1].pending = tuple(frames[-1].work.pending_revisions)
            if target_phase == "REQUIREMENTS":
                assert frames[-1].pending
        ref = save(
            records,
            project_id,
            entity_type,
            entity_id,
            record,
            actor_id,
            evidence_refs=evidence_refs,
        )
        if matched:
            frames[-1].ref = ref
            if point == "manifest":
                fail()
        return ref

    def observed_journal(
        records: RequestRecords, project: str, key: str, value: BaseModel, state: str = "RUNNING"
    ) -> None:
        record = value
        journal(records, project, key, record, state)
        frame = active()
        if (
            frame is not None
            and point == "applied"
            and isinstance(record, InputDelivery)
            and record.first_result_ref == frame.ref
        ):
            stored = records.journal_read(project, key, InputDelivery)
            assert stored is not None and stored.state == "APPLIED" and state == "APPLIED"
            fail()

    def observed_append(
        store: OperationJournal,
        *,
        project_id: str,
        operation_id: str,
        event_type: str,
        payload: dict[str, JsonValue],
    ) -> EventRecord:
        result = append(
            store,
            project_id=project_id,
            operation_id=operation_id,
            event_type=event_type,
            payload=payload,
        )
        if (
            active() is not None
            and point == "event"
            and event_type == "research.progress"
            and payload.get("phase") == target_phase
        ):
            with read_connection(runtime.ledger.engine) as connection:
                assert (
                    connection.execute(
                        select(events.c.event_id).where(events.c.event_id == result.event_id)
                    ).scalar_one()
                    == result.event_id
                )
            fail()
        return result

    def observed_complete(
        store: SqliteOperationStore,
        operation_id: str,
        result: dict[str, JsonValue],
        *,
        completed_at: datetime,
    ) -> OperationRecord:
        completed = complete(store, operation_id, result, completed_at=completed_at)
        if active() is not None and point == "complete":
            with read_connection(runtime.ledger.engine) as connection:
                assert (
                    connection.execute(
                        select(operations.c.state).where(operations.c.operation_id == operation_id)
                    ).scalar_one()
                    == "SUCCEEDED"
                )
            fail()
        return completed

    try:
        with monkeypatch.context() as patch:
            patch.setattr(SqliteAtomicUnitOfWork, "transaction", observed_transaction)
            patch.setattr(RequestRecords, "save", observed_save)
            patch.setattr(RequestRecords, "journal", observed_journal)
            patch.setattr(OperationJournal, "append", observed_append)
            patch.setattr(SqliteOperationStore, "complete", observed_complete)
            accepted = value(
                await runtime.bus.dispatch(
                    request(
                        "thread/start",
                        "publication-fault",
                        {
                            "project_id": "p",
                            "problem": "Compare the connected condition",
                            "contract_version": 2,
                        },
                    )
                )
            )
            await runtime.bus.drain()
        assert fired == [point] and len(rolled_back) == 1 and frames == []
        operation_id, thread_id = accepted["operation_id"], accepted["thread_id"]
        assert isinstance(operation_id, str) and isinstance(thread_id, str)
        operation = runtime.bus.read_operation(operation_id)
        assert (
            operation is not None
            and operation.state is OperationState.FAILED
            and operation.error is not None
        )
        thread = host.threads.read(thread_id)
        assert thread is not None and thread.execution_state is ThreadExecutionState.IDLE
        with runtime.ledger.engine.connect() as connection:
            event_types = list(
                connection.execute(
                    select(events.c.event_type).where(events.c.operation_id == operation_id)
                ).scalars()
            )
            assert "research.failed" in event_types
            assert (
                "research.terminal" not in event_types and "operation.succeeded" not in event_types
            )
            stored_checkpoints = connection.execute(
                select(checkpoints.c.payload_json).where(checkpoints.c.operation_id == operation_id)
            ).scalars()
            assert all(
                json.loads(str(payload)).get("completion") != "TERMINAL"
                for payload in stored_checkpoints
            )
        failed = rolled_back[0].ref
        assert failed is not None
        current = host.records.read("p", EntityType.DECISION_OBJECT, "result:" + thread_id)
        assert current is not None and current[0] != failed
        saved = snapshot(runtime.ledger.engine)
        if point == "complete":
            again = value(
                await runtime.bus.dispatch(
                    request(
                        "thread/input",
                        "publication-recovery",
                        {
                            "project_id": "p",
                            "thread_id": thread_id,
                            "instruction": "Review the same connected condition again",
                            "contract_version": 2,
                        },
                    )
                )
            )
            await runtime.bus.drain()
            identifier = again["operation_id"]
            assert isinstance(identifier, str)
            recovered = runtime.bus.read_operation(identifier)
            assert recovered is not None and recovered.state is OperationState.SUCCEEDED
            saved = snapshot(runtime.ledger.engine)
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path, model_resolver=ControlledResearchModel())
    try:
        assert_phase_delta(saved, snapshot(reopened.ledger.engine))
        assert reopened.ledger.read_revision_by_digest("p", failed.revision_digest) is None
    finally:
        reopened.close()
