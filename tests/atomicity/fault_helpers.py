"""Shared checks for the midway-failure tests: a failed command leaves only its own bookkeeping."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import Engine, select
from tests.atomicity.harness import (
    AllowedRow,
    Snapshot,
    assert_phase_delta,
    failed_command_allowances,
    snapshot,
)
from tests.integration.storage_coverage_helpers import request, value

from thoth.adapters.storage.resource_scope_schema import operation_resource_bindings
from thoth.adapters.storage.schema import operations
from thoth.apps.runtime import AppRuntime
from thoth.domain.resource_scope import OperationResourceBinding
from thoth.protocol.jsonrpc import JsonRpcRequest


def read_uses_of_failed_command(engine: Engine, command: JsonRpcRequest) -> tuple[str, ...]:
    """The resources the failed command read. A failed command must never have written one."""
    with engine.connect() as connection:
        identifier = connection.execute(
            select(operations.c.operation_id).where(
                operations.c.project_id == str(command.params.input["project_id"]),
                operations.c.method == command.method,
                operations.c.idempotency_key == command.params.meta.idempotency_key,
            )
        ).scalar_one()
        content = connection.execute(
            select(operation_resource_bindings.c.content_json).where(
                operation_resource_bindings.c.operation_id == identifier
            )
        ).scalar_one()
    binding = OperationResourceBinding.model_validate_json(content)
    assert {use.capability for use in binding.resource_uses} <= {"READ"}, binding.resource_uses
    return tuple(use.resource_ref for use in binding.resource_uses)


def assert_only_failed_command_bookkeeping(
    engine: Engine,
    before: Snapshot,
    command: JsonRpcRequest,
    *,
    survivors: tuple[AllowedRow, ...] = (),
) -> Snapshot:
    """Every table equals its state before the command except the failed command's own journal rows
    (and the `survivors` a test names as designed leftovers)."""
    allowed = failed_command_allowances(
        engine, command, expected_reads=read_uses_of_failed_command(engine, command)
    )
    after = snapshot(engine)
    assert_phase_delta(before, after, (*allowed, *survivors))
    return after


async def expect_rolled_back_and_retry(
    runtime: AppRuntime,
    monkeypatch: pytest.MonkeyPatch,
    store: type[object],
    point: str,
    method: str,
    body: dict[str, object],
    *,
    nth: int = 1,
) -> dict[str, Any]:
    """Fail right after the store's `nth` `point` write: nothing but the failed command's own
    journal rows may remain, and the same request must then succeed. Returns the retry's value."""
    command = request(method, "fault", body)
    before = snapshot(runtime.ledger.engine)
    with monkeypatch.context() as patch:
        hits = fail_nth(patch, store, point, nth)
        result = await runtime.bus.dispatch(command)
    assert result.error is not None, "the injected fault must surface"
    assert hits == [point] * nth, "the fault point was reached"
    assert_only_failed_command_bookkeeping(runtime.ledger.engine, before, command)
    return value(await runtime.bus.dispatch(request(method, "retry", body)))


def fail_nth(patch: pytest.MonkeyPatch, owner: type[object], method: str, nth: int) -> list[str]:
    """Let the first `nth` calls of `owner.method` write, then raise right after the `nth` write."""
    original = getattr(owner, method)
    hits: list[str] = []

    def fault(instance: object, *args: Any, **kwargs: Any) -> None:
        original(instance, *args, **kwargs)
        hits.append(method)
        if len(hits) == nth:
            raise RuntimeError(f"injected storage failure after write {nth} of {method}")

    patch.setattr(owner, method, fault)
    return hits
