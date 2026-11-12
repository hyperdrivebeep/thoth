"""A request the person stopped says so; one cancelled any other way keeps an unknown reason."""

import asyncio
from pathlib import Path
from typing import Any

import orjson
from pydantic import JsonValue
from tests.integration.progress_wait import wait_for_event_while_progressing
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup


async def _running(tmp_path: Path) -> tuple[Any, dict[str, object], str]:
    model = ControlledResearchModel(wait=True)
    runtime = await setup(tmp_path, model, source=False)
    accepted = value(
        await runtime.bus.dispatch(
            request(
                "thread/start",
                "start",
                {"project_id": "p", "problem": "Stop", "contract_version": 2},
            )
        )
    )
    await wait_for_event_while_progressing(
        model.started, lambda: len(model.calls), label="the request reaches its first model call"
    )
    scope: dict[str, object] = {"project_id": "p", "thread_id": accepted["thread_id"]}
    return runtime, scope, str(accepted["operation_id"])


async def _read(runtime: Any, scope: dict[str, object], key: str) -> dict[str, Any]:
    return dict(value(await runtime.bus.dispatch(request("thread/read", key, scope))))


async def _cancel_by_operation(runtime: Any, operation_id: str) -> None:
    value(
        await runtime.bus.dispatch(
            request("operation/cancel", "cancel", {"project_id": "p", "operation_id": operation_id})
        )
    )
    await asyncio.wait_for(runtime.bus.drain(), 12)


async def test_a_request_the_person_stopped_is_recorded_as_stopped_by_the_person(
    tmp_path: Path,
) -> None:
    runtime, scope, operation_id = await _running(tmp_path)
    try:
        running = await _read(runtime, scope, "read-running")
        assert running["operation_state"] == "RUNNING" and running["operation_error"] is None
        stopped = value(await runtime.bus.dispatch(request("thread/stop", "stop", scope)))
        assert stopped["cancelled"] is True
        await asyncio.wait_for(runtime.bus.drain(), 12)
        read = await _read(runtime, scope, "read-stopped")
        assert read["operation_state"] == "CANCELLED"
        assert read["operation_error"] == {
            "message": "USER_STOPPED",
            "data": {"reason_code": "USER_STOPPED"},
        }
        # The stored operation is untouched: the reason lives beside it, not in its error.
        operation = runtime.bus.read_operation(operation_id)
        assert operation is not None and operation.error is None
        again = await _read(runtime, scope, "read-again")
        assert again["operation_error"] == read["operation_error"]
    finally:
        runtime.close()


async def test_a_request_cancelled_any_other_way_keeps_an_unknown_reason(tmp_path: Path) -> None:
    runtime, scope, operation_id = await _running(tmp_path)
    try:
        await _cancel_by_operation(runtime, operation_id)
        read = await _read(runtime, scope, "read-cancelled")
        assert read["operation_state"] == "CANCELLED" and read["operation_error"] is None
    finally:
        runtime.close()


async def test_a_stop_after_the_request_was_cancelled_records_no_reason(tmp_path: Path) -> None:
    runtime, scope, operation_id = await _running(tmp_path)
    try:
        await _cancel_by_operation(runtime, operation_id)
        # The request was already cancelled: this stop ended nothing, so it takes no credit for it.
        value(await runtime.bus.dispatch(request("thread/stop", "late-stop", scope)))
        read = await _read(runtime, scope, "read-late")
        assert read["operation_state"] == "CANCELLED" and read["operation_error"] is None
    finally:
        runtime.close()


def test_a_stop_note_is_shown_only_for_an_operation_that_ended_cancelled() -> None:
    from types import SimpleNamespace
    from typing import cast

    from thoth.application.services.research_read_payload import operation_error
    from thoth.application.services.research_thread_read_snapshot import ResearchThreadReadSnapshot
    from thoth.domain.enums import OperationState
    from thoth.domain.research_stop import ResearchStopNote

    note = ResearchStopNote(operation_id="operation:x", requested_at="2026-11-12T00:00:00+00:00")

    def shown(state: OperationState, error: dict[str, Any] | None) -> object:
        operation = SimpleNamespace(state=state, error=error)
        snapshot = cast(
            ResearchThreadReadSnapshot, SimpleNamespace(operation=operation, stop_note=note)
        )
        return operation_error(snapshot)

    assert shown(OperationState.CANCELLED, None) == {
        "message": "USER_STOPPED",
        "data": {"reason_code": "USER_STOPPED"},
    }
    assert shown(OperationState.SUCCEEDED, None) is None  # it finished first: no reason is claimed
    assert shown(OperationState.FAILED, None) is None
    recorded = {"message": "other", "data": {"reason_code": "OTHER"}}
    assert shown(OperationState.CANCELLED, recorded) == recorded  # a recorded error is not replaced


async def _result(runtime: Any, operation_id: str, key: str) -> dict[str, Any]:
    return dict(
        value(
            await runtime.bus.query(
                request(
                    "operation/result/read", key, {"project_id": "p", "operation_id": operation_id}
                )
            )
        )
    )


async def test_the_turn_card_of_a_stopped_round_is_told_the_person_stopped_it(
    tmp_path: Path,
) -> None:
    """A round stopped before it saved a result has no result in the thread read; the conversation
    shows its turn from operation/result/read, so the reason has to be there too."""
    runtime, scope, operation_id = await _running(tmp_path)
    try:
        value(await runtime.bus.dispatch(request("thread/stop", "stop", scope)))
        await asyncio.wait_for(runtime.bus.drain(), 12)
        listed = value(await runtime.bus.query(request("thread/activity/list", "turns", scope)))
        turns = listed["conversation"]["turns"]
        assert [turn["operation_id"] for turn in turns] == [operation_id]
        earlier = await _result(runtime, turns[0]["operation_id"], "turn")
        assert earlier["state"] == "CANCELLED"
        assert earlier["error"] == {
            "message": "USER_STOPPED",
            "data": {"reason_code": "USER_STOPPED"},
        }
        assert await _result(runtime, operation_id, "turn-again") == earlier  # the same each read
        # A stopped thread takes no more input: nothing is asked after a stop on this thread.
        refused = await runtime.bus.dispatch(
            request(
                "thread/input", "again", {**scope, "instruction": "다시", "contract_version": 2}
            )
        )
        assert refused.error is not None
    finally:
        runtime.close()


async def test_a_round_cancelled_without_a_stop_keeps_an_unknown_reason_in_its_turn(
    tmp_path: Path,
) -> None:
    """A cancel that is not a stop leaves the round unexplained, in the turn card too."""
    runtime, _scope, first_op = await _running(tmp_path)
    try:
        await _cancel_by_operation(runtime, first_op)
        earlier = await _result(runtime, first_op, "turn")
        assert earlier["state"] == "CANCELLED" and earlier["error"] is None
    finally:
        runtime.close()


async def test_operation_results_other_than_a_stopped_research_request_are_unchanged(
    tmp_path: Path,
) -> None:
    runtime, _scope, operation_id = await _running(tmp_path)
    try:
        await _cancel_by_operation(runtime, operation_id)
        cancelled = dict(
            value(
                await runtime.bus.query(
                    request(
                        "operation/result/read",
                        "cancelled",
                        {"project_id": "p", "operation_id": operation_id},
                    )
                )
            )
        )
        assert cancelled["state"] == "CANCELLED" and cancelled["error"] is None
        # A reader without the research journal (as every other composition builds it) answers the
        # same bytes for the same operation.
        from thoth.application.commands.operations import OperationCommandHandlers

        stored = runtime.bus.read_operation(operation_id)
        assert stored is not None
        plain = OperationCommandHandlers(runtime.bus._operations)
        injected = OperationCommandHandlers(
            runtime.bus._operations,
            outcome_reasons=_NoReasons(),
        )
        body: dict[str, JsonValue] = {"project_id": "p", "operation_id": operation_id}
        assert orjson.dumps(await plain.result(body)) == orjson.dumps(await injected.result(body))
    finally:
        runtime.close()


class _NoReasons:
    def error_for(self, operation: object) -> None:
        return None


def test_the_operation_reader_adds_a_reason_only_to_a_cancelled_operation_with_a_stop_note() -> (
    None
):
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from typing import cast

    from thoth.application.services.research_stop_reason import ResearchStopReasons
    from thoth.domain.enums import OperationState
    from thoth.domain.operation import OperationRecord
    from thoth.domain.research_stop import ResearchStopNote
    from thoth.ports.control_record import ControlRecordStorePort

    note = ResearchStopNote(operation_id="operation:x", requested_at="2026-11-12T00:00:00+00:00")
    asked: list[tuple[str, str, str]] = []

    class Controls:
        def __init__(self, record: object) -> None:
            self.record = record

        def read(self, project_id: str, namespace: str, record_id: str) -> object:
            asked.append((project_id, namespace, record_id))
            return self.record

    def operation(state: OperationState, error: dict[str, Any] | None) -> OperationRecord:
        return OperationRecord(
            operation_id="operation:x",
            project_id="p",
            method="thread/start",
            idempotency_key="k",
            scope_digest="a" * 64,
            state=state,
            error=error,
            created_at=datetime(2026, 11, 12, tzinfo=UTC),
        )

    def reasons(record: object) -> ResearchStopReasons:
        return ResearchStopReasons(cast(ControlRecordStorePort, Controls(record)))

    written = SimpleNamespace(payload=note.model_dump(mode="python"))
    shown = reasons(written).error_for(operation(OperationState.CANCELLED, None))
    assert shown == {"message": "USER_STOPPED", "data": {"reason_code": "USER_STOPPED"}}
    assert asked == [("p", "RESEARCH_EXECUTION", "stop:operation:x")]
    asked.clear()
    # Anything else gets no answer, and a finished or failed operation is not even looked up.
    for state in (OperationState.SUCCEEDED, OperationState.FAILED, OperationState.RUNNING):
        assert reasons(written).error_for(operation(state, None)) is None
    assert reasons(written).error_for(operation(OperationState.CANCELLED, {"message": "x"})) is None
    assert reasons(None).error_for(operation(OperationState.CANCELLED, None)) is None
    assert len(asked) == 1  # only the cancelled operation without a stored error was looked up
