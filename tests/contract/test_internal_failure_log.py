"""An unexpected failure of a command leaves a log record a maintainer can find by operation id."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import JsonValue
from tests.contract.conftest import FixedClock, SequenceIds

from thoth.adapters.storage import SqliteLedger, SqliteOperationStore, migrate_sqlite_database
from thoth.domain.enums import OperationState
from thoth.protocol.bus import CommandBus
from thoth.protocol.jsonrpc import JsonRpcRequest, RpcApplicationError, RpcErrorCode
from thoth.protocol.registry import MethodRegistry

LOGGER = "thoth.internal_failure"
MARK = "SECRET-MARK-123 why did LAB-42 fail"


async def broken(payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
    # The kind of message that carries what the person typed.
    raise RuntimeError(f"cannot process {payload['description']}")


async def refused(payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
    del payload
    raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "PROJECT_NAME_TAKEN")


@pytest.fixture
def bus(tmp_path: Path) -> Iterator[tuple[CommandBus, SqliteOperationStore]]:
    migrate_sqlite_database(tmp_path / "thoth.sqlite3")
    ledger = SqliteLedger(tmp_path / "thoth.sqlite3")
    ledger.initialize()
    operations = SqliteOperationStore(ledger.engine)
    registry = MethodRegistry()
    registry.register("project/create", broken)
    registry.register("project/archive", refused)
    yield CommandBus(registry, operations, FixedClock(), SequenceIds()), operations
    ledger.close()


def _request(method: str, key: str) -> JsonRpcRequest:
    return JsonRpcRequest.model_validate(
        {
            "jsonrpc": "2.0",
            "id": key,
            "method": method,
            "params": {
                "_meta": {"idempotencyKey": key},
                "input": {"project_id": "project:lantern", "description": MARK},
            },
        }
    )


async def test_an_unexpected_failure_is_logged_with_its_operation_id_and_without_request_text(
    bus: tuple[CommandBus, SqliteOperationStore], caplog: pytest.LogCaptureFixture
) -> None:
    command_bus, operations = bus
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        response = await command_bus.dispatch(_request("project/create", "broken-1"))

    assert response.error is not None
    assert response.error.code == RpcErrorCode.INTERNAL_ERROR
    assert response.error.message == "internal command failure"
    operation_id = response.error.data["operation_id"]
    assert isinstance(operation_id, str)
    sealed = operations.read(operation_id)
    assert sealed is not None and sealed.state is OperationState.FAILED

    records = [record for record in caplog.records if record.name == LOGGER]
    assert len(records) == 1
    assert records[0].levelno == logging.ERROR
    text = records[0].getMessage()
    first, *detail = text.splitlines()
    assert first == (
        "internal command failure method=project/create "
        f"operation_id={operation_id} project_id=project:lantern"
    )
    assert detail[0] == "RuntimeError: cannot process [input]"
    assert any(line.endswith(" in broken") for line in detail)
    assert any(
        "thoth/protocol/bus.py:" in line and line.endswith(" in _execute") for line in detail
    )
    assert "SECRET-MARK-123" not in text and "LAB-42" not in text


async def test_a_typed_refusal_is_an_answer_and_is_not_logged_as_a_failure(
    bus: tuple[CommandBus, SqliteOperationStore], caplog: pytest.LogCaptureFixture
) -> None:
    command_bus, _ = bus
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        response = await command_bus.dispatch(_request("project/archive", "refused-1"))

    assert response.error is not None
    assert response.error.code == RpcErrorCode.DOMAIN_REJECTED
    assert [record for record in caplog.records if record.name == LOGGER] == []


async def test_the_same_key_sent_again_replays_the_failure_without_a_second_record(
    bus: tuple[CommandBus, SqliteOperationStore], caplog: pytest.LogCaptureFixture
) -> None:
    command_bus, _ = bus
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        first = await command_bus.dispatch(_request("project/create", "broken-2"))
        again = await command_bus.dispatch(_request("project/create", "broken-2"))

    assert first.error is not None and again.error is not None
    assert again.error.data["operation_id"] == first.error.data["operation_id"]
    assert len([record for record in caplog.records if record.name == LOGGER]) == 1
