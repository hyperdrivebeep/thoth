from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import Engine, select
from tests.integration.scoped_runtime import create_runtime

from thoth.adapters.storage.schema import metadata
from thoth.apps.runtime import AppRuntime
from thoth.protocol.jsonrpc import JsonRpcRequest, JsonRpcResponse

# Logical operation bookkeeping survives failed domain commands by contract.
_OPERATION_TABLES = {
    "operations",
    "idempotency_keys",
    "events",
    "checkpoints",
    "operation_resource_bindings",
}


def request(method: str, key: str, values: dict[str, object]) -> JsonRpcRequest:
    return JsonRpcRequest.model_validate(
        {
            "id": key,
            "method": method,
            "params": {"_meta": {"idempotencyKey": key}, "input": values},
        }
    )


def value(response: JsonRpcResponse) -> dict[str, Any]:
    assert response.error is None, response.error
    assert response.result is not None
    child = response.result["value"]
    assert isinstance(child, dict)
    return cast(dict[str, Any], child)


def domain_snapshot(engine: Engine) -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}
    with engine.connect() as connection:
        for name, table in sorted(metadata.tables.items()):
            if name in _OPERATION_TABLES:
                continue
            rows = connection.execute(select(table)).mappings()
            result[name] = tuple(
                sorted(json.dumps(dict(row), sort_keys=True, default=str) for row in rows)
            )
    return result


async def prepare_project(workspace: Path) -> tuple[AppRuntime, str]:
    runtime = create_runtime(workspace)
    project = "project:storage-coverage"
    value(
        await runtime.bus.dispatch(
            request(
                "project/create",
                "create-project",
                {
                    "project_id": project,
                    "name": "Storage coverage",
                    "cutoff_at": "2026-09-05T00:00:00Z",
                },
            )
        )
    )
    return runtime, project


def fail_after(monkeypatch: pytest.MonkeyPatch, owner: type[object], method: str) -> list[str]:
    original = cast(Callable[..., None], getattr(owner, method))
    hits: list[str] = []

    def fault(instance: object, *args: object, **kwargs: object) -> None:
        original(instance, *args, **kwargs)
        hits.append(method)
        raise RuntimeError("injected storage failure after " + method)

    monkeypatch.setattr(owner, method, fault)
    return hits
