"""Controlled journal state validates repair, rollback and fail-closed deferral."""

from collections.abc import Generator
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from pydantic import JsonValue

from thoth.application.services.operation_journal import OperationJournal
from thoth.domain.enums import OperationState
from thoth.domain.event import Checkpoint, EventRecord
from thoth.domain.operation import OperationRecord
from thoth.domain.resource_scope import ResourceScopeError
from thoth.ports.journal import JournalCompletionUnavailable
from thoth.ports.operation import OperationStorePort
from thoth.ports.resource_scope import ResourceAccessPort
from thoth.ports.runtime import AtomicUnitOfWorkPort
from thoth.protocol.journal_completion import JournalCompletion, operation_result_digest
from thoth.protocol.journal_completion_methods import SUPPLEMENTABLE_METHODS, SUPPORTED_RESULT_FORMS

BASE = datetime(2026, 10, 19, tzinfo=UTC)
METHOD = "project/metadata/update"


def test_measured_methods_and_saved_forms_match_reviewed_fixture() -> None:
    import json
    from pathlib import Path

    from thoth.protocol.read_queries import READ_QUERY_METHODS
    from thoth.protocol.registry import PUBLIC_METHODS

    fixture = json.loads(
        (Path(__file__).parents[1] / "fixtures/supplementable_methods.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(SUPPLEMENTABLE_METHODS) == 20
    assert sorted(SUPPLEMENTABLE_METHODS) == fixture["methods"]
    assert set(PUBLIC_METHODS) >= SUPPLEMENTABLE_METHODS
    assert not SUPPLEMENTABLE_METHODS.intersection(READ_QUERY_METHODS)
    assert not SUPPLEMENTABLE_METHODS.intersection(
        {
            "thread/start",
            "thread/input",
            "thread/steer",
            "thread/pause",
            "thread/resume",
            "thread/stop",
            "revision/restore",
            "revision/restore/apply",
            "revision/changeSet/commit",
        }
    )
    assert {
        method: sorted(sorted(keys) for keys in forms)
        for method, forms in SUPPORTED_RESULT_FORMS.items()
    } == fixture["result_key_shapes"]
    assert fixture["evidence"]["path"] == "outputs/refactor-vj-20261019/method-evidence.json"


class Clock:
    def __init__(self) -> None:
        self.value = BASE - timedelta(seconds=1)
        self.fixed = False
        self.calls = 0

    def now(self) -> datetime:
        value = self.value
        self.calls += 1
        if not self.fixed:
            self.value += timedelta(microseconds=1)
        return value


class Ids:
    def __init__(self) -> None:
        self.calls = 0
        self.tag = "z"

    def new(self, prefix: str) -> str:
        self.calls += 1
        return f"{prefix}:{self.tag}-{self.calls:05}"


class State:
    def __init__(self) -> None:
        result: dict[str, JsonValue] = dict.fromkeys(SUPPORTED_RESULT_FORMS[METHOD][0])
        result.update(schema_version="1.0.0", project_id="project:j-unit", name="Saved", revision=1)
        self.operation = OperationRecord(
            operation_id="operation:j-unit",
            project_id="project:j-unit",
            method=METHOD,
            idempotency_key="key",
            scope_digest="a" * 64,
            state=OperationState.SUCCEEDED,
            result=result,
            resource_uses=(),
            created_at=BASE - timedelta(seconds=1),
            completed_at=BASE,
        )
        self.events: list[EventRecord] = []
        self.points: list[Checkpoint] = []
        self.fail_write = False
        self.deny = False
        self.in_transaction = False

    def read(self, operation_id: str) -> OperationRecord:
        assert operation_id == self.operation.operation_id
        return self.operation

    def require_operation(self, operation: OperationRecord) -> None:
        assert self.in_transaction and operation.operation_id == self.operation.operation_id
        if self.deny:
            raise ResourceScopeError("RESOURCE_SCOPE_READ_DENIED")

    def list_events(self, operation_id: str) -> tuple[EventRecord, ...]:
        return tuple(sorted(self.events, key=lambda e: (e.created_at, e.event_id)))

    def list_checkpoints(self, operation_id: str) -> tuple[Checkpoint, ...]:
        return tuple(self.points)

    def latest_checkpoint(self, operation_id: str) -> Checkpoint | None:
        return self.points[-1] if self.points else None

    def insert_event(self, event: EventRecord) -> None:
        self.events.append(event)
        if self.fail_write:
            raise JournalCompletionUnavailable("synthetic write failure")

    def insert_checkpoint(self, checkpoint: Checkpoint) -> None:
        self.points.append(checkpoint)

    @contextmanager
    def transaction(self) -> Generator[None]:
        before = self.events[:], self.points[:]
        self.in_transaction = True
        try:
            yield
        except BaseException:
            self.events[:], self.points[:] = before
            raise
        finally:
            self.in_transaction = False

    @contextmanager
    def completion_transaction(self, uow: AtomicUnitOfWorkPort) -> Generator[None]:
        with uow.transaction():
            yield


def setup() -> tuple[State, Clock, Ids, OperationJournal, JournalCompletion]:
    state, clock, ids = State(), Clock(), Ids()
    journal = OperationJournal(state, clock, ids)
    journal.append(
        project_id=state.operation.project_id,
        operation_id=state.operation.operation_id,
        event_type="operation.started",
        payload={"method": METHOD},
    )
    clock.value = BASE + timedelta(seconds=10)
    completion = JournalCompletion(
        workspace_key="workspace:unit",
        pin_workspace=lambda: nullcontext(True),
        operations=cast(OperationStorePort, state),
        journal=journal,
        store=state,
        uow=state,
        resource_access=cast(ResourceAccessPort, state),
    )
    return state, clock, ids, journal, completion


def test_repair_is_marked_and_second_call_allocates_and_writes_nothing() -> None:
    state, clock, ids, _, completion = setup()
    original = state.operation.model_dump_json()
    outcome = completion.attempt(state.operation)
    assert outcome.status == "SUPPLEMENTED" and outcome.added == 3
    assert state.operation.model_dump_json() == original
    assert [e.event_type for e in state.events] == [
        "operation.started",
        "project/metadata/updated",
        "operation.succeeded",
    ]
    for item in [*state.events[1:], *state.points]:
        assert item.payload["supplemented"] is True
        assert item.payload["supplemented_for"] == BASE.isoformat()
        assert item.created_at >= BASE + timedelta(seconds=10)
    before = state.events[:], state.points[:], clock.calls, ids.calls
    assert completion.attempt(state.operation).status == "COMPLETE"
    assert (state.events, state.points, clock.calls, ids.calls) == before


@pytest.mark.parametrize("marked", [False, True])
def test_partial_original_or_supplemented_tail_keeps_existing_bytes(marked: bool) -> None:
    state, _, _, journal, completion = setup()
    assert state.operation.result is not None
    digest = operation_result_digest(state.operation.result)
    mark: dict[str, JsonValue] = (
        {"supplemented": True, "supplemented_for": BASE.isoformat()} if marked else {}
    )
    journal.checkpoint(
        operation_id=state.operation.operation_id,
        payload={"state": "SUCCEEDED", "result_digest": digest, **mark},
    )
    journal.append(
        project_id=state.operation.project_id,
        operation_id=state.operation.operation_id,
        event_type="project/metadata/updated",
        payload={"method": METHOD, "result_digest": digest, **mark},
    )
    before = (
        [e.model_dump_json() for e in state.events],
        [p.model_dump_json() for p in state.points],
    )
    outcome = completion.attempt(state.operation)
    assert outcome.status == "SUPPLEMENTED" and outcome.added == 1
    assert [e.model_dump_json() for e in state.events[:2]] == before[0]
    assert [p.model_dump_json() for p in state.points] == before[1]


@pytest.mark.parametrize(
    "case",
    [
        "unknown-method",
        "failed",
        "missing-result",
        "missing-completed-at",
        "unknown-shape",
        "unknown-schema",
    ],
)
def test_unsupported_saved_records_are_not_repaired(case: str) -> None:
    state, clock, ids, _, completion = setup()
    changes: dict[str, object] = {}
    if case == "unknown-method":
        changes["method"] = "revision/changeSet/commit"
    if case == "failed":
        changes.update(
            state=OperationState.FAILED, result=None, error={"reason_code": "OPERATION_OWNER_GONE"}
        )
    if case == "missing-result":
        changes["result"] = None
    if case == "missing-completed-at":
        changes["completed_at"] = None
    if case == "unknown-shape":
        changes["result"] = {"unknown": True}
    if case == "unknown-schema":
        changes["schema_version"] = "2.0.0"
    state.operation = state.operation.model_copy(update=changes)
    before = clock.calls, ids.calls, state.events[:], state.points[:]
    assert completion.attempt(state.operation).status in {"DEFERRED", "NOT_ELIGIBLE"}
    assert (clock.calls, ids.calls, state.events, state.points) == before


@pytest.mark.parametrize(
    "case", ["chain", "duplicate", "payload", "publisher", "checkpoints", "checkpoint-payload"]
)
def test_ambiguous_or_broken_existing_journal_defers_without_writes(case: str) -> None:
    state, clock, ids, journal, completion = setup()
    assert state.operation.result is not None
    digest = operation_result_digest(state.operation.result)
    if case == "chain":
        state.events[0] = state.events[0].model_copy(update={"event_digest": "f" * 64})
    if case in {"duplicate", "payload", "publisher"}:
        for _ in range(2 if case == "duplicate" else 1):
            journal.append(
                project_id=state.operation.project_id,
                operation_id=state.operation.operation_id,
                event_type="research.terminal"
                if case == "publisher"
                else "project/metadata/updated",
                payload={
                    "method": METHOD,
                    "result_digest": "f" * 64 if case == "payload" else digest,
                },
            )
    if case in {"checkpoints", "checkpoint-payload"}:
        for _ in range(2 if case == "checkpoints" else 1):
            journal.checkpoint(
                operation_id=state.operation.operation_id,
                payload={
                    "state": "SUCCEEDED",
                    "result_digest": "f" * 64 if case == "checkpoint-payload" else digest,
                },
            )
    before = state.events[:], state.points[:], clock.calls, ids.calls
    assert completion.attempt(state.operation).status == "DEFERRED"
    assert (state.events, state.points, clock.calls, ids.calls) == before


@pytest.mark.parametrize("case", ["rollback-clock", "equal-time-reversed-id", "storage"])
def test_new_batch_is_rolled_back_on_bad_order_or_storage_failure(case: str) -> None:
    state, clock, ids, _, completion = setup()
    if case == "rollback-clock":
        clock.value = BASE - timedelta(seconds=5)
    if case == "equal-time-reversed-id":
        clock.value, clock.fixed, ids.tag = state.events[0].created_at, True, "a"
    if case == "storage":
        state.fail_write = True
    before = state.events[:], state.points[:], state.operation.model_dump_json()
    outcome = completion.attempt(state.operation)
    assert outcome.status == "DEFERRED"
    assert (state.events, state.points, state.operation.model_dump_json()) == before


@pytest.mark.parametrize("case", ["denied", "canonical-change"])
def test_authority_and_canonical_failures_are_not_swallowed(case: str) -> None:
    state, _, _, _, completion = setup()
    original = state.operation
    if case == "denied":
        state.deny = True
    else:
        state.operation = original.model_copy(update={"scope_digest": "c" * 64})
    before = state.events[:], state.points[:]
    with pytest.raises(ResourceScopeError):
        completion.attempt(original)
    assert (state.events, state.points) == before
