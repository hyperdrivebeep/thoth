from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import pytest
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.apps.hosted_review_snapshot import fail_stale_running_sqlite, sqlite_path
from thoth.apps.runtime import create_runtime
from thoth.domain.deployment_mode import DeploymentMode
from thoth.domain.enums import OperationState
from thoth.domain.operation import OperationRecord
from thoth.protocol.jsonrpc import JsonRpcError


def _operation_error(operation: OperationRecord) -> JsonRpcError:
    assert operation.error is not None
    return JsonRpcError.model_validate(operation.error, strict=True)


def _record(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    assert all(isinstance(key, str) for key in cast(dict[object, object], value))
    return cast(dict[str, object], value)


def _text(value: object) -> str:
    assert isinstance(value, str)
    return value


@pytest.mark.asyncio
async def test_restore_fails_stale_running_without_success_or_rerun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", DeploymentMode.HOSTED_REVIEW.value)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    model = ControlledResearchModel(wait=True)
    runtime = await setup(tmp_path, model)
    try:
        accepted = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "start",
                    {
                        "project_id": "p",
                        "problem": "Explain LAB-42 latency.",
                        "contract_version": 2,
                    },
                )
            )
        )
        await model.started.wait()
        operation_id = _text(accepted["operation_id"])
        running = runtime.bus.read_operation(operation_id)
        assert running is not None
        assert running.state is OperationState.RUNNING
        calls_before_close = len(model.calls)
    finally:
        runtime.close()

    closed = fail_stale_running_sqlite(sqlite_path(tmp_path))
    assert closed >= 1
    reopened = create_runtime(
        tmp_path,
        deployment_mode=DeploymentMode.HOSTED_REVIEW,
        model_resolver=model,
    )
    try:
        operation = reopened.bus.read_operation(operation_id)
        assert operation is not None
        assert operation.state is OperationState.FAILED
        assert operation.result is None
        assert operation.error is not None
        error = _operation_error(operation)
        assert error.data["reason_code"] == "HOSTED_REVIEW_STALE_RUNNING"
        assert error.data["remote_observation"] == "UNKNOWN"
        readback = await reopened.bus.query(
            request(
                "thread/read",
                "read-after-stale-restore",
                {"project_id": "p", "thread_id": _text(accepted["thread_id"])},
            )
        )
        assert readback.error is None
        assert len(model.calls) == calls_before_close
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_second_start_is_rejected_while_research_is_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", DeploymentMode.HOSTED_REVIEW.value)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    model = ControlledResearchModel(wait=True)
    runtime = await setup(tmp_path, model)
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "first",
                    {
                        "project_id": "p",
                        "problem": "Explain LAB-42 latency.",
                        "contract_version": 2,
                    },
                )
            )
        )
        await model.started.wait()
        second = await runtime.bus.dispatch(
            request(
                "thread/start",
                "second",
                {
                    "project_id": "p",
                    "problem": "A second overlapping question.",
                    "contract_version": 2,
                },
            )
        )
        assert second.error is not None
        assert second.error.data["reason_code"] == "HOSTED_REVIEW_RESEARCH_LIMIT"
        model.release.set()
        await runtime.bus.drain()
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_thread_input_queues_behind_hosted_research_without_losing_first_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", DeploymentMode.HOSTED_REVIEW.value)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    model = ControlledResearchModel(wait=True)
    runtime = await setup(tmp_path, model, source=False)
    try:
        first = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "first",
                    {
                        "project_id": "p",
                        "problem": "Explain LAB-42 latency.",
                        "contract_version": 2,
                    },
                )
            )
        )
        await model.started.wait()
        second = await runtime.bus.dispatch(
            request(
                "thread/input",
                "input-while-running",
                {
                    "project_id": "p",
                    "thread_id": _text(first["thread_id"]),
                    "contract_version": 2,
                    "instruction": "Add a follow-up while first hosted run is still active.",
                },
            )
        )
        assert second.error is None
        assert second.result is not None
        assert _record(second.result["value"])["status"] == "QUEUED_AFTER_CURRENT"
        assert len(model.calls) == 1
        model.release.set()
        await runtime.bus.drain()
        first_operation = runtime.bus.read_operation(_text(first["operation_id"]))
        second_operation = runtime.bus.read_operation(_text(second.result["operation_id"]))
        assert first_operation is not None
        assert first_operation.state is OperationState.SUCCEEDED
        assert second_operation is not None
        assert second_operation.state is OperationState.SUCCEEDED
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_abandoned_running_same_key_does_not_reenter_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", DeploymentMode.HOSTED_REVIEW.value)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    model = ControlledResearchModel(wait=True)
    runtime = await setup(tmp_path, model)
    payload: dict[str, object] = {
        "project_id": "p",
        "problem": "Explain LAB-42 latency.",
        "contract_version": 2,
    }
    try:
        accepted = value(await runtime.bus.dispatch(request("thread/start", "start", payload)))
        await model.started.wait()
        operation_id = _text(accepted["operation_id"])
        running = runtime.bus.read_operation(operation_id)
        assert running is not None
        assert running.state is OperationState.RUNNING
        calls_before = len(model.calls)
        tasks: object = vars(runtime.bus).get("_research_tasks")
        assert isinstance(tasks, dict)
        assert all(
            isinstance(key, str) and isinstance(task, asyncio.Task)
            for key, task in cast(dict[object, object], tasks).items()
        )
        tasks.clear()
        replayed = await runtime.bus.dispatch(request("thread/start", "start", payload))
        assert replayed.error is not None
        assert replayed.error.data["reason_code"] == "HOSTED_REVIEW_STALE_RUNNING"
        assert replayed.error.data["remote_observation"] == "UNKNOWN"
        sealed = runtime.bus.read_operation(operation_id)
        assert sealed is not None
        assert sealed.state is OperationState.FAILED
        assert sealed.result is None
        assert len(model.calls) == calls_before
    finally:
        runtime.close()
