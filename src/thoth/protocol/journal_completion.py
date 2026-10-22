"""Supplement a measured success tail without repeating effects or changing its result."""

from collections import Counter
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import Literal

from pydantic import JsonValue

from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import OperationState
from thoth.domain.event import EventRecord
from thoth.domain.operation import OperationRecord
from thoth.domain.resource_scope import ResourceScopeError, resource_use_verification
from thoth.ports.journal import (
    JournalCompletionStorePort,
    JournalCompletionUnavailable,
    JournalPort,
)
from thoth.ports.operation import OperationStorePort
from thoth.ports.resource_scope import ResourceAccessPort
from thoth.ports.runtime import AtomicUnitOfWorkPort
from thoth.protocol.journal_completion_gate import journal_gate
from thoth.protocol.journal_completion_methods import SUPPLEMENTABLE_METHODS, supported_result
from thoth.protocol.notifications import notifications_for


def operation_result_digest(value: dict[str, JsonValue]) -> str:
    return domain_digest("OPERATION_RESULT", "1.0.0", canonical_payload(value))


@dataclass(frozen=True)
class CompletionOutcome:
    operation: OperationRecord
    status: Literal["NOT_ELIGIBLE", "COMPLETE", "SUPPLEMENTED", "DEFERRED"]
    added: int = 0
    reason: str | None = None


@dataclass(frozen=True)
class _Missing:
    checkpoint: bool
    notifications: tuple[str, ...]
    succeeded: bool

    @property
    def count(self) -> int:
        return int(self.checkpoint) + len(self.notifications) + int(self.succeeded)


class _Deferred(Exception):
    pass


def _payload_matches(
    actual: dict[str, object], base: dict[str, JsonValue], completed_at: str
) -> bool:
    if actual == base:
        return True
    return (
        actual == {**base, "supplemented": True, "supplemented_for": completed_at}
        and actual.get("supplemented") is True
    )


def _chain_valid(events: tuple[EventRecord, ...], operation: OperationRecord) -> bool:
    if len({event.event_id for event in events}) != len(events):
        return False
    previous = None
    for event in events:
        if (
            event.operation_id != operation.operation_id
            or event.project_id != operation.project_id
            or event.schema_version != "1.0.0"
            or event.previous_event_digest != previous
            or event.event_digest
            != domain_digest(
                "OPERATION_EVENT",
                "1.0.0",
                canonical_payload(event.model_dump(mode="python", exclude={"event_digest"})),
            )
        ):
            return False
        previous = event.event_digest
    return True


class JournalCompletion:
    def __init__(
        self,
        *,
        workspace_key: str,
        pin_workspace: Callable[[], AbstractContextManager[bool]],
        operations: OperationStorePort,
        journal: JournalPort,
        store: JournalCompletionStorePort,
        uow: AtomicUnitOfWorkPort,
        resource_access: ResourceAccessPort,
    ) -> None:
        self.workspace_key = workspace_key
        self._pin = pin_workspace
        self._operations = operations
        self._journal = journal
        self._store = store
        self._uow = uow
        self._access = resource_access

    def repair(self, operation: OperationRecord) -> OperationRecord:
        return self.attempt(operation).operation

    def attempt(self, operation: OperationRecord) -> CompletionOutcome:
        if (
            operation.state is not OperationState.SUCCEEDED
            or operation.method not in SUPPLEMENTABLE_METHODS
        ):
            return CompletionOutcome(operation, "NOT_ELIGIBLE")
        if (
            operation.result is None
            or operation.completed_at is None
            or operation.error is not None
            or operation.schema_version != "1.0.0"
            or not supported_result(operation.method, operation.result)
        ):
            return CompletionOutcome(operation, "DEFERRED", reason="UNSUPPORTED_SAVED_FORM")
        with journal_gate(self.workspace_key, operation.operation_id), self._pin() as held:
            if not held:
                return CompletionOutcome(operation, "DEFERRED", reason="WORKSPACE_NOT_OWNED")
            try:
                with (
                    resource_use_verification(operation.project_id),
                    self._store.completion_transaction(self._uow),
                ):
                    current = self._operations.read(operation.operation_id)
                    if current is None:
                        raise ResourceScopeError("OPERATION_RESOURCE_BINDING_INVALID")
                    self._access.require_operation(current)
                    if current.model_dump(mode="json") != operation.model_dump(mode="json"):
                        raise ResourceScopeError("OPERATION_RESOURCE_BINDING_INVALID")
                    missing = self._inspect(current)
                    if not missing.count:
                        return CompletionOutcome(operation, "COMPLETE")
                    self._write(current, missing)
                    if self._inspect(current).count:
                        raise _Deferred("INCOMPLETE_AFTER_APPEND")
                return CompletionOutcome(operation, "SUPPLEMENTED", added=missing.count)
            except _Deferred as exc:
                return CompletionOutcome(operation, "DEFERRED", reason=str(exc))
            except JournalCompletionUnavailable:
                return CompletionOutcome(operation, "DEFERRED", reason="STORAGE_UNAVAILABLE")

    def _inspect(self, operation: OperationRecord) -> _Missing:
        assert operation.result is not None and operation.completed_at is not None
        digest = operation_result_digest(operation.result)
        completed_at = operation.completed_at.isoformat()
        expected = notifications_for(operation.method, operation.result)
        events = self._store.list_events(operation.operation_id)
        points = self._store.list_checkpoints(operation.operation_id)
        if not events:
            raise _Deferred("MISSING_STARTED")
        if not _chain_valid(events, operation):
            raise _Deferred("UNSAFE_EVENT_CHAIN_OR_ORDER")
        if events[0].event_type != "operation.started":
            raise _Deferred("MISSING_STARTED")
        counts = Counter(event.event_type for event in events)
        if any(count != 1 for count in counts.values()):
            raise _Deferred("DUPLICATE_EVENT")
        for event in events:
            if event.event_type == "operation.started":
                if event.payload != {"method": operation.method}:
                    raise _Deferred("UNSUPPORTED_STARTED_PAYLOAD")
                continue
            if event.event_type == "operation.succeeded":
                base: dict[str, JsonValue] = {"result_digest": digest}
            elif event.event_type in expected:
                base = {"method": operation.method, "result_digest": digest}
            else:
                raise _Deferred("OTHER_PUBLISHER_EVENT")
            if not _payload_matches(event.payload, base, completed_at):
                raise _Deferred("EVENT_PAYLOAD_MISMATCH")
        if len(points) > 1:
            raise _Deferred("MULTIPLE_CHECKPOINTS")
        if points:
            point = points[0]
            if (
                point.operation_id != operation.operation_id
                or not _payload_matches(
                    point.payload, {"state": "SUCCEEDED", "result_digest": digest}, completed_at
                )
                or point.checkpoint_digest
                != domain_digest(
                    "OPERATION_CHECKPOINT",
                    "1.0.0",
                    canonical_payload(
                        point.model_dump(mode="python", exclude={"checkpoint_digest"})
                    ),
                )
            ):
                raise _Deferred("CHECKPOINT_PAYLOAD_MISMATCH")
        return _Missing(
            not points,
            tuple(name for name in expected if name not in counts),
            "operation.succeeded" not in counts,
        )

    def _write(self, operation: OperationRecord, missing: _Missing) -> None:
        assert operation.result is not None and operation.completed_at is not None
        digest = operation_result_digest(operation.result)
        mark: dict[str, JsonValue] = {
            "supplemented": True,
            "supplemented_for": operation.completed_at.isoformat(),
        }
        if missing.checkpoint:
            self._journal.checkpoint(
                operation_id=operation.operation_id,
                payload={"state": "SUCCEEDED", "result_digest": digest, **mark},
            )
        for notification in missing.notifications:
            self._journal.append(
                project_id=operation.project_id,
                operation_id=operation.operation_id,
                event_type=notification,
                payload={"method": operation.method, "result_digest": digest, **mark},
            )
        if missing.succeeded:
            self._journal.append(
                project_id=operation.project_id,
                operation_id=operation.operation_id,
                event_type="operation.succeeded",
                payload={"result_digest": digest, **mark},
            )


@contextmanager
def journal_tail_guard(
    completion: JournalCompletion | None, operation: OperationRecord
) -> Generator[None]:
    if completion is None or operation.method not in SUPPLEMENTABLE_METHODS:
        yield
    else:
        with journal_gate(completion.workspace_key, operation.operation_id):
            yield
