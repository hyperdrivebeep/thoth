from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from pydantic import JsonValue

from thoth.adapters.http.app import create_app
from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.storage import SqliteLedger, SqliteOperationStore, migrate_sqlite_database
from thoth.protocol.bus import CommandBus
from thoth.protocol.registry import MethodRegistry


def rpc(actor: str) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": f"local-{actor}",
        "method": "receipt/seal",
        "params": {
            "_meta": {"idempotencyKey": f"local-{actor}"},
            "input": {
                "project_id": "project:local",
                "actor_or_agent_ref": actor,
            },
        },
    }


@pytest.mark.asyncio
async def test_local_loopback_rejects_spoofed_actor_before_command_bus_io(
    tmp_path: Path,
) -> None:
    database = tmp_path / "thoth.sqlite3"
    migrate_sqlite_database(database)
    ledger = SqliteLedger(database)
    ledger.initialize()
    operations = SqliteOperationStore(ledger.engine)
    calls: list[dict[str, JsonValue]] = []

    async def handler(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        calls.append(value)
        return {"sealed": True}

    registry = MethodRegistry()
    registry.register("receipt/seal", handler)
    bus = CommandBus(registry, operations, SystemClock(), UuidIdGenerator())
    transport = httpx.ASGITransport(app=create_app(bus))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            denied = await client.post("/rpc", json=rpc("human:spoof"))

        payload = denied.json()
        assert payload["error"]["code"] == -32040
        assert payload["error"]["data"]["reason_code"] == "LOCAL_ACTOR_OVERRIDE_DENIED"
        assert calls == []
        assert operations.read("local-human:spoof") is None
    finally:
        ledger.close()


@pytest.mark.asyncio
async def test_local_loopback_accepts_fixed_actor_without_claiming_authentication(
    tmp_path: Path,
) -> None:
    database = tmp_path / "thoth.sqlite3"
    migrate_sqlite_database(database)
    ledger = SqliteLedger(database)
    ledger.initialize()
    operations = SqliteOperationStore(ledger.engine)

    async def handler(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return {
            "actor": value["actor_or_agent_ref"],
            "provenance_state": "PARTIAL",
        }

    registry = MethodRegistry()
    registry.register("receipt/seal", handler)
    bus = CommandBus(registry, operations, SystemClock(), UuidIdGenerator())
    transport = httpx.ASGITransport(app=create_app(bus))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            accepted = await client.post("/rpc", json=rpc("human:local-user"))

        payload = accepted.json()
        assert payload["result"]["value"]["actor"] == "human:local-user"
        assert payload["result"]["value"]["provenance_state"] == "PARTIAL"
    finally:
        ledger.close()
