"""A real completion must replay the sealing winner, never announce a false success."""

import asyncio
import json
from collections.abc import Callable, Generator
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager, contextmanager
from datetime import datetime
from pathlib import Path
from threading import Event
from typing import cast

import pytest
from pydantic import JsonValue
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import prepare_project, request

from thoth.adapters.storage.events import SqliteEventStore
from thoth.adapters.storage.operations import SqliteOperationStore
from thoth.domain.enums import OperationState
from thoth.domain.operation import OperationRecord
from thoth.protocol.bus import DispatchTicket
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


@pytest.mark.parametrize(
    ("outcome", "winner"),
    [(name, "seal") for name in ["success", "rpc-error", "permission", "validation", "internal"]]
    + [("success", "completion")],
)
async def test_sealing_winner_is_replayed_by_original_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    record_property: Callable[[str, object], None],
    outcome: str,
    winner: str,
) -> None:
    workspace = tmp_path / "workspace"
    original, project = await prepare_project(workspace)
    command = request(
        "project/metadata/update",
        "complete-race",
        {
            "project_id": project,
            "expected_revision": 0,
            "name": "Saved once",
        },
    )
    if outcome != "success":

        async def rejected(values: dict[str, JsonValue]) -> dict[str, JsonValue]:
            del values
            if outcome == "rpc-error":
                raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "synthetic rejection")
            if outcome == "permission":
                raise PermissionError("SYNTHETIC_AUTHORITY_REJECTED")
            if outcome == "validation":
                OperationRecord.model_validate({})
            raise RuntimeError("synthetic internal failure")

        original.bus._registry.decorate("project/metadata/update", lambda handler: rejected)  # pyright: ignore[reportPrivateUsage]
    ticket = original.bus.claim(command)
    assert isinstance(ticket, DispatchTicket) and ticket.operation.owner is not None
    identifier = ticket.operation.operation_id
    before_complete, resume_complete = Event(), Event()
    complete = SqliteOperationStore.complete
    fail = SqliteOperationStore.fail

    def paused_complete(
        self: SqliteOperationStore,
        operation_id: str,
        result: dict[str, JsonValue],
        *,
        completed_at: datetime,
    ) -> OperationRecord:
        if operation_id == identifier:
            before_complete.set()
            assert resume_complete.wait(timeout=10), "completion gate was not released"
        return complete(self, operation_id, result, completed_at=completed_at)

    monkeypatch.setattr(SqliteOperationStore, "complete", paused_complete)

    def paused_fail(
        self: SqliteOperationStore,
        operation_id: str,
        error: dict[str, JsonValue],
        *,
        completed_at: datetime,
    ) -> OperationRecord:
        if operation_id == identifier and self._engine is original.ledger.engine:  # pyright: ignore[reportPrivateUsage]
            before_complete.set()
            assert resume_complete.wait(timeout=10), "failure gate was not released"
        return fail(self, operation_id, error, completed_at=completed_at)

    monkeypatch.setattr(SqliteOperationStore, "fail", paused_fail)
    peer = None
    closed = False
    pin_reached, allow_pin = Event(), Event()
    with ThreadPoolExecutor(max_workers=2) as executor:
        future = executor.submit(asyncio.run, original.bus.execute(ticket))
        try:
            assert before_complete.wait(timeout=10), "original handler never reached completion"
            # close() retires this runtime's worker while its general completion is waiting.
            original.close()
            closed = True
            peer = create_runtime(workspace)
            if winner == "completion":
                policy = vars(peer.bus)["_owner"]
                original_pin = cast(
                    Callable[[], AbstractContextManager[bool]], vars(policy)["_pin_workspace"]
                )

                @contextmanager
                def gated_pin() -> Generator[bool]:
                    with original_pin() as held:
                        pin_reached.set()
                        assert allow_pin.wait(timeout=10)
                        yield held

                monkeypatch.setattr(policy, "_pin_workspace", gated_pin)
                sealing = executor.submit(asyncio.run, peer.bus.dispatch(command))
                assert pin_reached.wait(timeout=10)
                resume_complete.set()
                completed = future.result(timeout=10)
                allow_pin.set()
                sealed = sealing.result(timeout=10)
            else:
                sealed = await peer.bus.dispatch(command)
                assert sealed.error is not None
                assert sealed.error.data["reason_code"] == "OPERATION_OWNER_GONE"
                resume_complete.set()
                completed = future.result(timeout=10)
            stored = SqliteOperationStore(peer.ledger.engine).read(identifier)
            events = SqliteEventStore(peer.ledger.engine).list_events(identifier)
            observed = {
                "original_outcome": outcome,
                "winner": winner,
                "stored_state": None if stored is None else stored.state.value,
                "sealing_response_error": sealed.error is not None,
                "original_response_error": completed.error is not None,
                "original_result": completed.result,
                "events": [item.event_type for item in events],
            }
            record_property("race_observation", json.dumps(observed))
            print(json.dumps(observed))
            assert completed == sealed and stored is not None
            with peer.ledger.engine.connect() as connection:
                binding = json.loads(
                    connection.exec_driver_sql(
                        "SELECT content_json FROM operation_resource_bindings WHERE operation_id=?",
                        (identifier,),
                    ).scalar_one()
                )
            if winner == "completion":
                assert completed.error is None and stored.state is OperationState.SUCCEEDED
                assert binding["output_kind"] == "RESULT"
                assert [item.event_type for item in events] == [
                    "operation.started",
                    "project/metadata/updated",
                    "operation.succeeded",
                ]
            else:
                assert sealed.error is not None
                assert stored.error == sealed.error.model_dump(mode="json")
                assert binding["output_kind"] == "ERROR"
                assert [item.event_type for item in events] == [
                    "operation.started",
                    "operation.failed",
                ]
        finally:
            allow_pin.set()
            resume_complete.set()
            future.result(timeout=10)
            if peer is not None:
                peer.close()
            if not closed:
                original.close()
