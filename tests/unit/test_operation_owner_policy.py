"""Every owner predicate fails closed; the approved list and wire text are exact."""

import json
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from thoth.domain.deployment_mode import STALE_RUNNING_MESSAGE, STALE_RUNNING_REASON
from thoth.domain.enums import OperationState
from thoth.domain.operation import OperationOwnerRecord, OperationRecord
from thoth.ports.journal import JournalPort
from thoth.ports.operation import OperationStorePort
from thoth.ports.runtime import AtomicUnitOfWorkPort, ClockPort
from thoth.protocol.jsonrpc import JsonRpcError, JsonRpcRequest, RpcErrorCode
from thoth.protocol.operation_owner import (
    SEALABLE_METHODS,
    OperationOwner,
    owner_gone_error,
    stale_running_error,
)
from thoth.protocol.registry import PUBLIC_METHODS


@dataclass
class Worker:
    alive: bool | None = False
    worker_id: str = "worker:current"
    process_id: int = 20
    process_marker: str = "marker:current"
    calls: int = 0

    def is_alive(self, process_id: int, process_marker: str, worker_id: str) -> bool | None:
        self.calls += 1
        assert (process_id, process_marker, worker_id) == (10, "marker:old", "worker:old")
        return self.alive

    def close(self) -> None:
        pass


def operation() -> OperationRecord:
    return OperationRecord(
        operation_id="operation:policy",
        project_id="project:policy",
        method="project/metadata/update",
        idempotency_key="key",
        scope_digest="a" * 64,
        created_at=datetime(2026, 10, 19, tzinfo=UTC),
        state=OperationState.RUNNING,
        owner=OperationOwnerRecord(
            worker_id="worker:old", process_id=10, process_marker="marker:old", host_token="b" * 64
        ),
    )


def policy(
    worker: Worker, token: str | None = "b" * 64, held: bool = True, pin: bool = True
) -> OperationOwner:
    return OperationOwner(
        worker=worker,
        host_token=token,
        holds_workspace=lambda: held,
        pin_workspace=lambda: nullcontext(pin),
        operations=cast(OperationStorePort, object()),
        uow=cast(AtomicUnitOfWorkPort, object()),
        journal=cast(JournalPort, object()),
        clock=cast(ClockPort, object()),
    )


@pytest.mark.parametrize(
    "case",
    [
        "eligible",
        "alive",
        "unknown",
        "no-owner",
        "no-token",
        "different-token",
        "old-null-token",
        "old-unknown-marker",
        "current-unknown-marker",
        "no-lock",
        "pending",
        "terminal",
        "excluded",
        "method-mismatch",
    ],
)
def test_all_conditions_are_required(case: str) -> None:
    worker, original = Worker(), operation()
    token: str | None = "b" * 64
    method = original.method
    if case == "alive":
        worker.alive = True
    if case == "unknown":
        worker.alive = None
    if case == "no-owner":
        original = original.model_copy(update={"owner": None})
    if case == "no-token":
        token = None
    if case == "different-token":
        token = "c" * 64
    if case in {"old-null-token", "old-unknown-marker"}:
        assert original.owner is not None
        update = {"host_token": None} if case == "old-null-token" else {"process_marker": "UNKNOWN"}
        original = original.model_copy(update={"owner": original.owner.model_copy(update=update)})
    if case == "current-unknown-marker":
        worker.process_marker = "UNKNOWN"
    if case == "pending":
        original = original.model_copy(update={"state": OperationState.PENDING})
    if case == "terminal":
        original = original.model_copy(update={"state": OperationState.SUCCEEDED})
    if case == "excluded":
        method = "thread/resume"
        original = original.model_copy(update={"method": method})
    if case == "method-mismatch":
        method = "project/activate"
    subject = policy(worker, token, held=case != "no-lock")
    assert subject.gone(original, method) is (case == "eligible")
    if case not in {"eligible", "alive", "unknown"}:
        assert worker.calls == 0
    if case in {"no-token", "current-unknown-marker"}:
        assert subject.record() is None


def test_lock_lost_before_pin_and_different_key_cannot_write() -> None:
    def unmeasured(*args: object) -> None:
        pass

    original = operation()
    request = JsonRpcRequest.model_validate(
        {
            "id": "key",
            "method": original.method,
            "params": {"_meta": {"idempotencyKey": "key"}, "input": {}},
        }
    )
    assert policy(Worker(), pin=False).seal(request, original, unmeasured) is None
    other = request.model_copy(
        update={
            "params": request.params.model_copy(
                update={
                    "meta": request.params.meta.model_copy(update={"idempotency_key": "different"}),
                }
            )
        }
    )
    assert policy(Worker()).seal(other, original, unmeasured) is None


def test_list_and_bilingual_error_are_exact() -> None:
    fixture = json.loads((Path(__file__).parents[1] / "fixtures/sealable_methods.json").read_text())
    assert frozenset(fixture["methods"]) == SEALABLE_METHODS and len(SEALABLE_METHODS) == 67
    assert set(PUBLIC_METHODS) >= SEALABLE_METHODS
    expected = (
        "이전에 보낸 같은 요청을 실행하던 THOTH가 끝나서 요청이 마무리되지 못했습니다. "
        "변경이 저장됐는지 확인한 뒤 필요하면 새 요청으로 다시 보내세요. / "
        "The THOTH process that was running this request ended before it finished. "
        "Check whether the change was saved, then send it again as a new request "
        "if you still need it."
    )
    assert owner_gone_error().message == expected
    assert owner_gone_error().data == {
        "reason_code": "OPERATION_OWNER_GONE",
        "remote_observation": "UNKNOWN",
        "new_request_required": True,
    }
    assert (
        stale_running_error().model_dump_json()
        == JsonRpcError(
            code=RpcErrorCode.DOMAIN_REJECTED,
            message=STALE_RUNNING_MESSAGE,
            data={"reason_code": STALE_RUNNING_REASON, "remote_observation": "UNKNOWN"},
        ).model_dump_json()
    )
