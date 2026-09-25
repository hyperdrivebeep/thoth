"""Normal v2 admission keeps RUNNING input behind the current request."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from pydantic import BaseModel
from tests.integration.scoped_runtime import fixture_scope_policy
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.adapters.http.app import create_app
from thoth.adapters.storage.control_record import SqliteControlRecordStore
from thoth.adapters.storage.operations import SqliteOperationStore
from thoth.adapters.storage.sqlite import SqliteLedger
from thoth.application.commands.research_threads import ResearchThreadHandlers
from thoth.apps.hosted_review_snapshot import fail_stale_running_sqlite, sqlite_path
from thoth.apps.runtime import AppRuntime, create_runtime
from thoth.domain.deployment_mode import DeploymentMode
from thoth.domain.enums import ModelRole, OperationState
from thoth.domain.research_queue import QueuedResearchInput
from thoth.domain.research_request import ResearchAttempt
from thoth.protocol.deferred import PendingExecution
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


def _research_handler(runtime: AppRuntime) -> ResearchThreadHandlers:
    handler = getattr(runtime.bus._registry.resolve("thread/input"), "__self__", None)  # pyright: ignore[reportPrivateUsage]
    assert isinstance(handler, ResearchThreadHandlers)
    return handler


async def test_restarted_same_key_current_attempt_never_enters_queue(tmp_path: Path) -> None:
    model = ControlledResearchModel()
    runtime = await setup(tmp_path, model, source=False)
    first = value(
        await runtime.bus.dispatch(
            request(
                "thread/start",
                "first",
                {"project_id": "p", "problem": "First", "contract_version": 2},
            )
        )
    )
    await runtime.bus.drain()
    model.started.clear()
    model.release.clear()
    payload: dict[str, object] = {
        "project_id": "p",
        "thread_id": str(first["thread_id"]),
        "instruction": "Later question",
        "contract_version": 2,
    }
    active = value(await runtime.bus.dispatch(request("thread/input", "same-current", payload)))
    await model.started.wait()
    runtime.bus.close_tasks()
    await runtime.bus.drain()
    runtime.close()

    reopened = create_runtime(
        tmp_path, model_resolver=model, resource_scope_policy=fixture_scope_policy()
    )
    try:
        operation = reopened.bus.read_operation(str(active["operation_id"]))
        assert operation is not None and operation.state is OperationState.RUNNING
        replayed = value(
            await reopened.bus.dispatch(request("thread/input", "same-current", payload))
        )
        assert replayed["operation_id"] == active["operation_id"]
        handler = _research_handler(reopened)
        assert handler.queue.read(operation) is None
    finally:
        model.release.set()
        reopened.bus.close_tasks()
        await reopened.bus.drain()
        reopened.close()


async def test_current_attempt_reentry_stays_ahead_of_later_queued_input(tmp_path: Path) -> None:
    model = ControlledResearchModel()
    runtime = await setup(tmp_path, model, source=False)
    first = value(
        await runtime.bus.dispatch(
            request(
                "thread/start",
                "first",
                {"project_id": "p", "problem": "First", "contract_version": 2},
            )
        )
    )
    await runtime.bus.drain()
    model.started.clear()
    model.release.clear()
    payload: dict[str, object] = {
        "project_id": "p",
        "thread_id": str(first["thread_id"]),
        "instruction": "Current question",
        "contract_version": 2,
    }
    active = value(await runtime.bus.dispatch(request("thread/input", "current", payload)))
    await model.started.wait()
    later = value(
        await runtime.bus.dispatch(
            request("thread/input", "later", {**payload, "instruction": "Later question"})
        )
    )
    assert later["after_operation_id"] == active["operation_id"]
    runtime.bus.close_tasks()
    await runtime.bus.drain()
    runtime.close()

    reopened = create_runtime(
        tmp_path, model_resolver=model, resource_scope_policy=fixture_scope_policy()
    )
    try:
        repeated = value(await reopened.bus.dispatch(request("thread/input", "current", payload)))
        assert repeated["operation_id"] == active["operation_id"]
        handler = _research_handler(reopened)
        current_op = reopened.bus.read_operation(str(active["operation_id"]))
        later_op = reopened.bus.read_operation(str(later["operation_id"]))
        assert current_op is not None and later_op is not None
        assert handler.queue.read(current_op) is None
        persisted_later = handler.queue.read(later_op)
        assert persisted_later is not None
        assert persisted_later.after_operation_id == active["operation_id"]
        assert persisted_later.ordinal == 1
        assert len(handler.queue.store.list("p", str(first["thread_id"]))) == 1
    finally:
        reopened.bus.close_tasks()
        await reopened.bus.drain()
        reopened.close()


async def test_existing_attempt_replay_takes_precedence_over_legacy_self_edge(
    tmp_path: Path,
) -> None:
    model = ControlledResearchModel()
    runtime = await setup(tmp_path, model, source=False)
    first = value(
        await runtime.bus.dispatch(
            request(
                "thread/start",
                "first",
                {"project_id": "p", "problem": "First", "contract_version": 2},
            )
        )
    )
    await runtime.bus.drain()
    model.started.clear()
    model.release.clear()
    payload: dict[str, object] = {
        "project_id": "p",
        "thread_id": str(first["thread_id"]),
        "instruction": "Current question",
        "contract_version": 2,
    }
    active = value(await runtime.bus.dispatch(request("thread/input", "current", payload)))
    await model.started.wait()
    later = value(
        await runtime.bus.dispatch(
            request("thread/input", "later", {**payload, "instruction": "Later question"})
        )
    )
    handler = _research_handler(runtime)
    later_operation = runtime.bus.read_operation(str(later["operation_id"]))
    assert later_operation is not None
    later_item = handler.queue.read(later_operation)
    assert later_item is not None
    handler.queue.store.save(
        later_item.model_copy(
            update={
                "operation_id": str(active["operation_id"]),
                "idempotency_key": "current",
                "input_id": "input:legacy-self-edge",
                "ordinal": 2,
                "after_operation_id": str(active["operation_id"]),
            }
        )
    )
    runtime.bus.close_tasks()
    await runtime.bus.drain()
    runtime.close()

    reopened = create_runtime(
        tmp_path, model_resolver=model, resource_scope_policy=fixture_scope_policy()
    )
    try:
        current_operation = reopened.bus.read_operation(str(active["operation_id"]))
        assert current_operation is not None
        current_handler = _research_handler(reopened)
        recorded_self_edge = current_handler.queue.read(current_operation)
        assert recorded_self_edge is not None
        replayed = value(await reopened.bus.dispatch(request("thread/input", "current", payload)))
        assert replayed["operation_id"] == active["operation_id"]
        assert replayed["status"] != "HOLD"
        assert current_handler.queue.read(current_operation) == recorded_self_edge
        assert len(current_handler.queue.store.list("p", str(first["thread_id"]))) == 2
    finally:
        reopened.bus.close_tasks()
        await reopened.bus.drain()
        reopened.close()


async def test_same_key_reentry_checks_owner_before_queue_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = ControlledResearchModel()
    runtime = await setup(tmp_path, model, source=False)
    first = value(
        await runtime.bus.dispatch(
            request(
                "thread/start",
                "first",
                {"project_id": "p", "problem": "First", "contract_version": 2},
            )
        )
    )
    await runtime.bus.drain()
    model.started.clear()
    model.release.clear()
    payload: dict[str, object] = {
        "project_id": "p",
        "thread_id": str(first["thread_id"]),
        "instruction": "Current question",
        "contract_version": 2,
    }
    active = value(await runtime.bus.dispatch(request("thread/input", "current", payload)))
    await model.started.wait()
    runtime.bus.close_tasks()
    await runtime.bus.drain()
    runtime.close()

    reopened = create_runtime(
        tmp_path, model_resolver=model, resource_scope_policy=fixture_scope_policy()
    )
    try:
        handler = _research_handler(reopened)
        operation = reopened.bus.read_operation(str(active["operation_id"]))
        assert operation is not None
        before = reopened.ledger.read_heads("p")

        def denied(_operation: object) -> None:
            raise RpcApplicationError(
                RpcErrorCode.AUTHORIZATION_DENIED, "AUTH_OPERATION_OWNER_DENIED"
            )

        monkeypatch.setattr(handler.operation_access, "require_execution_owner", denied)
        response = await reopened.bus.dispatch(request("thread/input", "current", payload))
        assert response.error is not None
        assert response.error.code == RpcErrorCode.AUTHORIZATION_DENIED
        assert handler.queue.read(operation) is None
        assert reopened.ledger.read_heads("p") == before
    finally:
        reopened.bus.close_tasks()
        await reopened.bus.drain()
        reopened.close()


async def test_current_operation_without_attempt_is_bounded_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = ControlledResearchModel(wait=True)
    runtime = await setup(tmp_path, model, source=False)
    try:
        first = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "first",
                    {"project_id": "p", "problem": "First", "contract_version": 2},
                )
            )
        )
        await model.started.wait()
        handler = _research_handler(runtime)
        operation = runtime.bus.read_operation(str(first["operation_id"]))
        assert operation is not None
        original = handler.records.journal_read

        def missing[T: BaseModel](project_id: str, key: str, model_type: type[T]) -> T | None:
            if key == operation.operation_id and model_type is ResearchAttempt:
                return None
            return original(project_id, key, model_type)

        monkeypatch.setattr(handler.records, "journal_read", missing)
        before = runtime.ledger.read_heads("p")
        pending = handler.queue.accept_if_busy(
            handler,
            operation,
            {
                "project_id": "p",
                "thread_id": str(first["thread_id"]),
                "instruction": "Should not create another input",
            },
        )
        assert isinstance(pending, PendingExecution)
        assert pending.value["status"] == "HOLD"
        assert pending.value["reason"] == "CURRENT_OPERATION_ATTEMPT_MISSING"
        assert handler.queue.read(operation) is None
        assert runtime.ledger.read_heads("p") == before
    finally:
        runtime.bus.close_tasks()
        await runtime.bus.drain()
        runtime.close()


async def test_persisted_self_queue_projects_hold_without_rewriting_record(
    tmp_path: Path,
) -> None:
    model = ControlledResearchModel(wait=True)
    runtime = await setup(tmp_path, model, source=False)
    first = value(
        await runtime.bus.dispatch(
            request(
                "thread/start",
                "first",
                {"project_id": "p", "problem": "First", "contract_version": 2},
            )
        )
    )
    await model.started.wait()
    payload: dict[str, object] = {
        "project_id": "p",
        "thread_id": str(first["thread_id"]),
        "instruction": "Later question",
        "contract_version": 2,
    }
    queued = value(await runtime.bus.dispatch(request("thread/input", "queued", payload)))
    handler = _research_handler(runtime)
    operation = runtime.bus.read_operation(str(queued["operation_id"]))
    assert operation is not None
    item = handler.queue.read(operation)
    assert item is not None
    handler.queue.store.save(
        item.model_copy(update={"after_operation_id": item.operation_id})
    )
    runtime.bus.close_tasks()
    await runtime.bus.drain()
    runtime.close()

    reopened = create_runtime(
        tmp_path, model_resolver=model, resource_scope_policy=fixture_scope_policy()
    )
    try:
        record_store = SqliteControlRecordStore(reopened.ledger.engine)
        before = record_store.read("p", "RESEARCH_EXECUTION", f"queue:{queued['operation_id']}")
        handler = _research_handler(reopened)
        queued_operation = reopened.bus.read_operation(str(queued["operation_id"]))
        assert queued_operation is not None
        projection = handler.queue.admission(queued_operation)
        assert projection is not None and projection["status"] == "HOLD"
        summary = handler.queue.summary("p", str(first["thread_id"]))
        assert summary[0]["state"] == "HOLD"
        assert summary[0]["hold_reason"] == "QUEUE_SELF_DEPENDENCY"
        waited = await asyncio.wait_for(
            handler.queue.wait_then_run(handler, queued_operation.operation_id), timeout=2
        )
        assert isinstance(waited, PendingExecution)
        assert waited.value["hold_reason"] == "QUEUE_SELF_DEPENDENCY"
        replayed = value(await reopened.bus.dispatch(request("thread/input", "queued", payload)))
        assert replayed["status"] == "HOLD"
        assert replayed["hold_reason"] == "QUEUE_SELF_DEPENDENCY"
        await asyncio.wait_for(reopened.bus.drain(), timeout=2)
        after = record_store.read("p", "RESEARCH_EXECUTION", f"queue:{queued['operation_id']}")
        assert before == after
        current = reopened.bus.read_operation(str(queued["operation_id"]))
        assert current is not None and current.state is OperationState.RUNNING
        assert len(model.calls) == 1
    finally:
        reopened.bus.close_tasks()
        await reopened.bus.drain()
        reopened.close()


async def test_hosted_http_accepts_same_thread_queue_without_superseding_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", DeploymentMode.HOSTED_REVIEW.value)
    monkeypatch.setenv("THOTH_REVIEW_SESSION_ID", "queue-http-session")
    model = ControlledResearchModel(wait=True)
    runtime = await setup(tmp_path, model, source=False)
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(runtime.bus)),
        base_url="http://test",
        headers={"x-thoth-review-session": "queue-http-session"},
    )
    try:
        first_response = await client.post(
            "/rpc",
            json={
                "id": "first",
                "method": "thread/start",
                "params": {
                    "_meta": {"idempotencyKey": "first"},
                    "input": {
                        "project_id": "p",
                        "problem": "First question",
                        "contract_version": 2,
                    },
                },
            },
        )
        assert first_response.status_code == 200
        first = first_response.json()["result"]["value"]
        await model.started.wait()
        head_key = f"THREAD:request:{first['thread_id']}"
        first_head = runtime.ledger.read_heads("p")[head_key]
        queued_response = await client.post(
            "/rpc",
            json={
                "id": "queued",
                "method": "thread/input",
                "params": {
                    "_meta": {"idempotencyKey": "queued"},
                    "input": {
                        "project_id": "p",
                        "thread_id": first["thread_id"],
                        "instruction": "Second question",
                        "contract_version": 2,
                    },
                },
            },
        )
        assert queued_response.status_code == 200
        queued = queued_response.json()["result"]["value"]
        assert queued["status"] == "QUEUED_AFTER_CURRENT"
        assert runtime.ledger.read_heads("p")[head_key] == first_head
        assert len(model.calls) == 1
        model.release.set()
        await runtime.bus.drain()
        first_operation = runtime.bus.read_operation(first["operation_id"])
        queued_operation = runtime.bus.read_operation(queued["operation_id"])
        assert first_operation is not None and first_operation.state is OperationState.SUCCEEDED
        assert queued_operation is not None and queued_operation.state is OperationState.SUCCEEDED
    finally:
        await client.aclose()
        runtime.close()


async def test_running_input_keeps_first_head_and_runs_after_terminal(tmp_path: Path) -> None:
    model = ControlledResearchModel(wait=True)
    runtime = await setup(tmp_path, model, source=False)
    try:
        first = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "first",
                    {"project_id": "p", "problem": "First question", "contract_version": 2},
                )
            )
        )
        await model.started.wait()
        thread_id = str(first["thread_id"])
        head_key = f"THREAD:request:{thread_id}"
        original_head = runtime.ledger.read_heads("p")[head_key]
        queued = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "queued-one",
                    {
                        "project_id": "p",
                        "thread_id": thread_id,
                        "instruction": "Second question",
                        "contract_version": 2,
                    },
                )
            )
        )
        assert queued["status"] == "QUEUED_AFTER_CURRENT"
        assert queued["request_ref"] is None
        assert runtime.ledger.read_heads("p")[head_key] == original_head
        assert len(model.calls) == 1
        running_read = value(
            await runtime.bus.query(
                request("thread/read", "running-read", {"project_id": "p", "thread_id": thread_id})
            )
        )
        assert running_read["queued_inputs_v2"][0]["operation_id"] == queued["operation_id"]
        model.release.set()
        await runtime.bus.drain()
        first_operation = runtime.bus.read_operation(str(first["operation_id"]))
        second_operation = runtime.bus.read_operation(str(queued["operation_id"]))
        assert first_operation is not None and first_operation.state is OperationState.SUCCEEDED
        assert second_operation is not None and second_operation.state is OperationState.SUCCEEDED
        assert sum(call.role == ModelRole.RESEARCH_PLANNER for call in model.calls) == 2
        readback = value(
            await runtime.bus.query(
                request("thread/read", "read", {"project_id": "p", "thread_id": thread_id})
            )
        )
        assert readback["request_epoch"] == 2
    finally:
        runtime.close()


async def test_two_queued_inputs_are_ordered_and_same_key_is_idempotent(tmp_path: Path) -> None:
    model = ControlledResearchModel(wait=True)
    runtime = await setup(tmp_path, model, source=False)
    try:
        first = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "first",
                    {"project_id": "p", "problem": "First question", "contract_version": 2},
                )
            )
        )
        await model.started.wait()
        thread_id = str(first["thread_id"])
        payload: dict[str, object] = {
            "project_id": "p",
            "thread_id": thread_id,
            "instruction": "Second question",
            "contract_version": 2,
        }
        second = value(await runtime.bus.dispatch(request("thread/input", "second", payload)))
        repeated = value(await runtime.bus.dispatch(request("thread/input", "second", payload)))
        third = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "third",
                    {**payload, "instruction": "Third question"},
                )
            )
        )
        assert second["operation_id"] == repeated["operation_id"]
        assert second["input_id"] == repeated["input_id"]
        assert (second["ordinal"], third["ordinal"]) == (1, 2)
        assert third["after_operation_id"] == second["operation_id"]
        model.release.set()
        await runtime.bus.drain()
        assert sum(call.role == ModelRole.RESEARCH_PLANNER for call in model.calls) == 3
        for item in (first, second, third):
            operation = runtime.bus.read_operation(str(item["operation_id"]))
            assert operation is not None and operation.state is OperationState.SUCCEEDED
        calls_after_completion = len(model.calls)
        completed_replay = await runtime.bus.dispatch(request("thread/input", "second", payload))
        assert completed_replay.error is None
        assert completed_replay.result is not None
        assert completed_replay.result["operation_id"] == second["operation_id"]
        assert len(model.calls) == calls_after_completion
        readback = value(
            await runtime.bus.query(
                request("thread/read", "read", {"project_id": "p", "thread_id": thread_id})
            )
        )
        assert readback["request_epoch"] == 3
    finally:
        runtime.close()


async def test_two_local_runtimes_commit_one_queue_activation(tmp_path: Path) -> None:
    model = ControlledResearchModel(wait=True)
    first_runtime = await setup(tmp_path, model, source=False)
    second_runtime = None
    try:
        first = value(
            await first_runtime.bus.dispatch(
                request(
                    "thread/start",
                    "first",
                    {"project_id": "p", "problem": "First question", "contract_version": 2},
                )
            )
        )
        await model.started.wait()
        payload: dict[str, object] = {
            "project_id": "p",
            "thread_id": str(first["thread_id"]),
            "instruction": "Second question",
            "contract_version": 2,
        }
        queued = value(await first_runtime.bus.dispatch(request("thread/input", "queued", payload)))
        second_runtime = create_runtime(
            tmp_path,
            model_resolver=model,
            resource_scope_policy=fixture_scope_policy(),
        )
        repeated = value(
            await second_runtime.bus.dispatch(request("thread/input", "queued", payload))
        )
        assert repeated["input_id"] == queued["input_id"]
        model.release.set()
        await asyncio.gather(first_runtime.bus.drain(), second_runtime.bus.drain())
        operation = first_runtime.bus.read_operation(str(queued["operation_id"]))
        assert operation is not None and operation.state is OperationState.SUCCEEDED
        assert sum(call.role == ModelRole.RESEARCH_PLANNER for call in model.calls) == 2
        requests = first_runtime.ledger.read_revisions(
            "p", "THREAD", f"request:{first['thread_id']}"
        )
        assert len(requests) == 2
    finally:
        if second_runtime is not None:
            second_runtime.close()
        first_runtime.close()


async def test_failed_predecessor_holds_queue_without_second_model_call(tmp_path: Path) -> None:
    model = ControlledResearchModel(wait=True)
    runtime = await setup(tmp_path, model, source=False)
    try:
        first = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "first",
                    {"project_id": "p", "problem": "First question", "contract_version": 2},
                )
            )
        )
        await model.started.wait()
        queued = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "queued",
                    {
                        "project_id": "p",
                        "thread_id": str(first["thread_id"]),
                        "instruction": "Later question",
                        "contract_version": 2,
                    },
                )
            )
        )
        runtime.bus.fail_if_running(
            str(first["operation_id"]),
            {"code": -32010, "message": "simulated predecessor failure"},
        )
        model.release.set()
        await runtime.bus.drain()
        record = SqliteControlRecordStore(runtime.ledger.engine).read(
            "p", "RESEARCH_EXECUTION", f"queue:{queued['operation_id']}"
        )
        assert record is not None
        held = QueuedResearchInput.model_validate(record.payload)
        assert held.state == "HOLD" and held.hold_reason == "PREDECESSOR_NOT_SUCCEEDED"
        assert sum(call.role == ModelRole.RESEARCH_PLANNER for call in model.calls) == 1
    finally:
        runtime.close()


async def test_queued_replace_holds_when_prior_queued_input_advances_epoch(tmp_path: Path) -> None:
    model = ControlledResearchModel(wait=True)
    runtime = await setup(tmp_path, model, source=False)
    try:
        first = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "first",
                    {"project_id": "p", "problem": "First question", "contract_version": 2},
                )
            )
        )
        await model.started.wait()
        thread_id = str(first["thread_id"])
        value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "append",
                    {
                        "project_id": "p",
                        "thread_id": thread_id,
                        "instruction": "Append this",
                        "contract_version": 2,
                    },
                )
            )
        )
        replacement = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "replace",
                    {
                        "project_id": "p",
                        "thread_id": thread_id,
                        "instruction": "Replace old question",
                        "edit_kind": "REPLACE",
                        "expected_request_epoch": 1,
                        "contract_version": 2,
                    },
                )
            )
        )
        model.release.set()
        await runtime.bus.drain()
        record = SqliteControlRecordStore(runtime.ledger.engine).read(
            "p", "RESEARCH_EXECUTION", f"queue:{replacement['operation_id']}"
        )
        assert record is not None
        held = QueuedResearchInput.model_validate(record.payload)
        assert held.state == "HOLD" and held.hold_reason == "REQUEST_EPOCH_CHANGED"
        assert sum(call.role == ModelRole.RESEARCH_PLANNER for call in model.calls) == 2
    finally:
        runtime.close()


@pytest.mark.parametrize("malformed_epoch", [True, False, "1", None])
async def test_queued_replace_rejects_non_strict_integer_epoch_without_queuing(
    tmp_path: Path, malformed_epoch: object
) -> None:
    model = ControlledResearchModel(wait=True)
    runtime = await setup(tmp_path, model, source=False)
    try:
        first = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "first",
                    {"project_id": "p", "problem": "First question", "contract_version": 2},
                )
            )
        )
        await model.started.wait()
        thread_id = str(first["thread_id"])
        handler = _research_handler(runtime)
        before = runtime.ledger.read_heads("p")
        response = await runtime.bus.dispatch(
            request(
                "thread/input",
                "invalid-replace",
                {
                    "project_id": "p",
                    "thread_id": thread_id,
                    "instruction": "Should not replace",
                    "edit_kind": "REPLACE",
                    "expected_request_epoch": malformed_epoch,
                    "contract_version": 2,
                },
            )
        )
        assert response.error is not None
        assert response.error.code == RpcErrorCode.DOMAIN_REJECTED
        assert response.error.message == "REQUEST_EPOCH_CONFLICT"
        assert handler.queue.summary("p", thread_id) == ()
        assert runtime.ledger.read_heads("p") == before
        original = runtime.bus.read_operation(str(first["operation_id"]))
        assert original is not None and original.state is OperationState.RUNNING
        assert len(model.calls) == 1
    finally:
        runtime.bus.close_tasks()
        await runtime.bus.drain()
        runtime.close()


async def test_cancel_queued_operation_records_hold_without_running_it(tmp_path: Path) -> None:
    model = ControlledResearchModel(wait=True)
    runtime = await setup(tmp_path, model, source=False)
    try:
        first = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "first",
                    {"project_id": "p", "problem": "First question", "contract_version": 2},
                )
            )
        )
        await model.started.wait()
        queued = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "queued",
                    {
                        "project_id": "p",
                        "thread_id": str(first["thread_id"]),
                        "instruction": "Later question",
                        "contract_version": 2,
                    },
                )
            )
        )
        cancelled = value(
            await runtime.bus.dispatch(
                request(
                    "operation/cancel",
                    "cancel-queued",
                    {"project_id": "p", "operation_id": queued["operation_id"]},
                )
            )
        )
        assert cancelled["cancelled"] is True
        model.release.set()
        await runtime.bus.drain()
        record = SqliteControlRecordStore(runtime.ledger.engine).read(
            "p", "RESEARCH_EXECUTION", f"queue:{queued['operation_id']}"
        )
        assert record is not None
        held = QueuedResearchInput.model_validate(record.payload)
        assert held.state == "HOLD" and held.hold_reason == "QUEUE_OPERATION_CANCELLED"
        assert sum(call.role == ModelRole.RESEARCH_PLANNER for call in model.calls) == 1
    finally:
        runtime.close()


async def test_restart_preserves_queued_receipt_without_automatic_model_reentry(
    tmp_path: Path,
) -> None:
    model = ControlledResearchModel(wait=True)
    runtime = await setup(tmp_path, model, source=False)
    first = value(
        await runtime.bus.dispatch(
            request(
                "thread/start",
                "first",
                {"project_id": "p", "problem": "First question", "contract_version": 2},
            )
        )
    )
    await model.started.wait()
    payload: dict[str, object] = {
        "project_id": "p",
        "thread_id": str(first["thread_id"]),
        "instruction": "Later question",
        "contract_version": 2,
    }
    queued = value(await runtime.bus.dispatch(request("thread/input", "queued", payload)))
    calls_before_close = len(model.calls)
    runtime.bus.close_tasks()
    await runtime.bus.drain()
    runtime.close()
    fail_stale_running_sqlite(sqlite_path(tmp_path))
    reopened = create_runtime(
        tmp_path,
        deployment_mode=DeploymentMode.HOSTED_REVIEW,
        model_resolver=model,
    )
    try:
        operation = reopened.bus.read_operation(str(queued["operation_id"]))
        assert operation is not None and operation.state is OperationState.RUNNING
        assert len(model.calls) == calls_before_close
        replayed = value(await reopened.bus.dispatch(request("thread/input", "queued", payload)))
        assert replayed["input_id"] == queued["input_id"]
        await reopened.bus.drain()
        record = SqliteControlRecordStore(reopened.ledger.engine).read(
            "p", "RESEARCH_EXECUTION", f"queue:{queued['operation_id']}"
        )
        assert record is not None
        held = QueuedResearchInput.model_validate(record.payload)
        assert held.state == "HOLD"
        assert len(model.calls) == calls_before_close
    finally:
        reopened.close()


async def test_startup_reconciles_cancelled_queue_operation_after_crash_gap(
    tmp_path: Path,
) -> None:
    model = ControlledResearchModel(wait=True)
    runtime = await setup(tmp_path, model, source=False)
    first = value(
        await runtime.bus.dispatch(
            request(
                "thread/start",
                "first",
                {"project_id": "p", "problem": "First question", "contract_version": 2},
            )
        )
    )
    await model.started.wait()
    queued = value(
        await runtime.bus.dispatch(
            request(
                "thread/input",
                "queued",
                {
                    "project_id": "p",
                    "thread_id": str(first["thread_id"]),
                    "instruction": "Later question",
                    "contract_version": 2,
                },
            )
        )
    )
    runtime.bus.close_tasks()
    await runtime.bus.drain()
    runtime.close()
    ledger = SqliteLedger(sqlite_path(tmp_path))
    try:
        SqliteOperationStore(ledger.engine).cancel(
            str(queued["operation_id"]), completed_at=datetime.now(UTC)
        )
    finally:
        ledger.close()
    reopened = create_runtime(
        tmp_path,
        model_resolver=model,
        resource_scope_policy=fixture_scope_policy(),
    )
    try:
        record = SqliteControlRecordStore(reopened.ledger.engine).read(
            "p", "RESEARCH_EXECUTION", f"queue:{queued['operation_id']}"
        )
        assert record is not None
        held = QueuedResearchInput.model_validate(record.payload)
        assert held.state == "HOLD" and held.hold_reason == "QUEUE_OPERATION_CANCELLED"
        assert sum(call.role == ModelRole.RESEARCH_PLANNER for call in model.calls) == 1
    finally:
        reopened.close()


async def test_steer_holds_queued_input_and_fences_old_result(tmp_path: Path) -> None:
    model = ControlledResearchModel(wait=True)
    runtime = await setup(tmp_path, model, source=False)
    try:
        first = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "first",
                    {"project_id": "p", "problem": "First question", "contract_version": 2},
                )
            )
        )
        await model.started.wait()
        thread_id = str(first["thread_id"])
        queued = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "queued",
                    {
                        "project_id": "p",
                        "thread_id": thread_id,
                        "instruction": "Queued question",
                        "contract_version": 2,
                    },
                )
            )
        )
        steered = value(
            await runtime.bus.dispatch(
                request(
                    "thread/steer",
                    "steer",
                    {
                        "project_id": "p",
                        "thread_id": thread_id,
                        "instruction": "Change direction now",
                        "expected_request_epoch": 1,
                        "contract_version": 2,
                    },
                )
            )
        )
        assert steered["request_epoch"] == 2
        assert len(model.calls) == 1
        model.release.set()
        await runtime.bus.drain()
        record = SqliteControlRecordStore(runtime.ledger.engine).read(
            "p", "RESEARCH_EXECUTION", f"queue:{queued['operation_id']}"
        )
        assert record is not None
        item = QueuedResearchInput.model_validate(record.payload)
        assert item is not None and item.state == "SUPERSEDED"
        assert item.hold_reason == "STEER_CHANGED_DIRECTION"
        assert sum(call.role == ModelRole.RESEARCH_PLANNER for call in model.calls) == 2
        first_operation = runtime.bus.read_operation(str(first["operation_id"]))
        assert first_operation is not None and first_operation.state is OperationState.CANCELLED
        readback = value(
            await runtime.bus.query(
                request("thread/read", "read", {"project_id": "p", "thread_id": thread_id})
            )
        )
        assert readback["current_result"]["operation_id"] == steered["operation_id"]
    finally:
        runtime.close()
