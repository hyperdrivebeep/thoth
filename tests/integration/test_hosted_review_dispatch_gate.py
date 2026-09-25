from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.adapters.http.app import create_app
from thoth.apps.hosted_review_dispatch import (
    SNAPSHOT_UNCOMMITTED,
    hosted_dispatch,
    wait_for_hosted_dispatch,
)
from thoth.domain.deployment_mode import DeploymentMode
from thoth.domain.enums import OperationState
from thoth.domain.operation import OperationRecord
from thoth.protocol.jsonrpc import JsonRpcError, RpcApplicationError


def _operation_error(operation: OperationRecord) -> JsonRpcError:
    assert operation.error is not None
    return JsonRpcError.model_validate(operation.error, strict=True)


@pytest.fixture(autouse=True)
def reset_hosted_dispatch() -> Iterator[None]:
    hosted_dispatch.disable()
    hosted_dispatch.timeout_seconds = 20.0
    yield
    hosted_dispatch.disable()
    hosted_dispatch.timeout_seconds = 20.0


@pytest.mark.asyncio
async def test_research_waits_for_snapshot_commit_before_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", DeploymentMode.HOSTED_REVIEW.value)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    hosted_dispatch.enable()
    hosted_dispatch.timeout_seconds = 2.0
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
        operation_id = str(accepted["operation_id"])
        await asyncio.sleep(0.05)
        assert model.started.is_set() is False
        assert len(model.calls) == 0
        hosted_dispatch.release(operation_id)
        await asyncio.wait_for(model.started.wait(), 5)
        assert len(model.calls) >= 1
        model.release.set()
        await runtime.bus.drain()
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_uncommitted_snapshot_fails_without_model_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", DeploymentMode.HOSTED_REVIEW.value)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    hosted_dispatch.enable()
    hosted_dispatch.timeout_seconds = 0.05
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
        operation_id = str(accepted["operation_id"])
        await runtime.bus.drain()
        operation = runtime.bus.read_operation(operation_id)
        assert operation is not None
        assert operation.state is OperationState.FAILED
        assert operation.result is None
        assert operation.error is not None
        error = _operation_error(operation)
        assert error.data["reason_code"] == SNAPSHOT_UNCOMMITTED
        assert error.data["remote_observation"] == "NOT_SENT"
        assert error.data["pre_io"] is True
        assert model.calls == []
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_release_before_wait_still_runs_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", DeploymentMode.HOSTED_REVIEW.value)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    hosted_dispatch.enable()
    hosted_dispatch.release("start-op")
    # The actual operation id is generated; pre-release of a different id must not leak.
    hosted_dispatch.timeout_seconds = 2.0
    model = ControlledResearchModel()
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
        hosted_dispatch.release(str(accepted["operation_id"]))
        await runtime.bus.drain()
        operation = runtime.bus.read_operation(str(accepted["operation_id"]))
        assert operation is not None
        assert operation.state is OperationState.SUCCEEDED
        assert model.calls
    finally:
        runtime.close()


def test_dispatch_release_endpoint_requires_internal_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", DeploymentMode.HOSTED_REVIEW.value)
    monkeypatch.setenv("THOTH_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("THOTH_REVIEW_SESSION_SECRET", "internal-secret")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with TestClient(create_app()) as client:
        assert (
            client.post("/internal/dispatch-release", json={"operation_id": "op"}).status_code
            == 404
        )
        released = client.post(
            "/internal/dispatch-release",
            headers={"x-thoth-review-internal": "internal-secret"},
            json={"operation_id": "op-1"},
        )
        assert released.status_code == 200
        assert released.json() == {"released": "op-1"}
        assert client.post(
            "/internal/dispatch-release",
            headers={"x-thoth-review-internal": "internal-secret"},
            json={},
        ).status_code == 400
        assert (
            client.post("/internal/dispatch-abort", json={"operation_id": "op"}).status_code
            == 404
        )
        aborted = client.post(
            "/internal/dispatch-abort",
            headers={
                "x-thoth-review-internal": "internal-secret",
                "x-thoth-review-session": "review-session",
            },
            json={"operation_id": "op-missing"},
        )
        assert aborted.status_code == 200
        assert aborted.json()["aborted"] == "op-missing"


def test_create_app_skips_dispatch_gate_without_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", DeploymentMode.HOSTED_REVIEW.value)
    monkeypatch.setenv("THOTH_WORKSPACE", str(tmp_path))
    monkeypatch.delenv("HOSTED_REVIEW_DISPATCH_GATE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with TestClient(create_app()):
        assert hosted_dispatch.enabled is False


def test_create_app_enables_dispatch_gate_when_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", DeploymentMode.HOSTED_REVIEW.value)
    monkeypatch.setenv("THOTH_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("HOSTED_REVIEW_DISPATCH_GATE", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with TestClient(create_app()):
        assert hosted_dispatch.enabled is True


@pytest.mark.asyncio
async def test_abort_fails_running_research_without_model_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", DeploymentMode.HOSTED_REVIEW.value)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    hosted_dispatch.enable()
    hosted_dispatch.timeout_seconds = 2.0
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
        operation_id = str(accepted["operation_id"])
        await asyncio.sleep(0.05)
        assert model.calls == []
        hosted_dispatch.abort(operation_id)
        failed = runtime.bus.fail_if_running(
            operation_id,
            {
                "code": -32002,
                "message": SNAPSHOT_UNCOMMITTED,
                "data": {
                    "reason_code": SNAPSHOT_UNCOMMITTED,
                    "remote_observation": "NOT_SENT",
                    "pre_io": True,
                },
            },
        )
        assert failed is not None
        assert failed.state is OperationState.FAILED
        await runtime.bus.drain()
        operation = runtime.bus.read_operation(operation_id)
        assert operation is not None
        assert operation.state is OperationState.FAILED
        assert operation.error is not None
        error = _operation_error(operation)
        assert error.data["reason_code"] == SNAPSHOT_UNCOMMITTED
        assert error.data["remote_observation"] == "NOT_SENT"
        assert model.calls == []
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_abort_unblocks_wait_as_uncommitted() -> None:
    hosted_dispatch.enable()
    hosted_dispatch.timeout_seconds = 2.0
    waiter = asyncio.create_task(wait_for_hosted_dispatch("op-abort"))
    await asyncio.sleep(0.01)
    hosted_dispatch.abort("op-abort")
    with pytest.raises(RpcApplicationError, match=SNAPSHOT_UNCOMMITTED):
        await waiter
