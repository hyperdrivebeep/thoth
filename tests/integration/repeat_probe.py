"""Measurement helper for the state-transition repeat tests (V-S): not a test.

`RepeatProbe` wraps `CommandBus.dispatch`. After a target command succeeded inside an existing flow
test it sends the same input again under a NEW idempotency key and records what that repeat did:
whether it was refused, which domain tables it changed, and which notifications it emitted. The
database is then restored from a backup taken before the repeat, so the flow under test never sees
the probe (same method as the V-B measurement). A repeat under a new key is what a person meets
when they press a button twice or re-send after an interruption.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Awaitable, Callable
from contextlib import closing
from dataclasses import dataclass, field
from typing import Any

import pytest
from tests.integration.storage_coverage_helpers import domain_snapshot

from thoth.protocol.bus import CommandBus
from thoth.protocol.jsonrpc import JsonRpcRequest, JsonRpcResponse


@dataclass(frozen=True)
class Repeat:
    method: str
    accepted: bool
    error_message: str | None
    changed_tables: frozenset[str]
    added_rows: dict[str, int]
    notifications: tuple[str, ...]
    unchanged_reason: str | None = None
    cancelled: object = None


@dataclass
class RepeatProbe:
    methods: frozenset[str]
    # Optionally change the repeated input in place (e.g. another decision or scope).
    change: Callable[[str, dict[str, Any]], None] | None = None
    results: dict[str, list[Repeat]] = field(default_factory=dict)  # type: ignore[type-arg]
    _active: bool = False

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        original = CommandBus.dispatch
        probe = self

        async def dispatch(bus: CommandBus, request: JsonRpcRequest) -> JsonRpcResponse:
            response = await original(bus, request)
            if (
                probe._active  # pyright: ignore[reportPrivateUsage]
                or request.method not in probe.methods
                or response.error is not None
            ):
                return response
            probe._active = True  # pyright: ignore[reportPrivateUsage]
            try:
                probe.results.setdefault(request.method, []).append(
                    await probe._repeat(bus, original, request, probe.change)  # pyright: ignore[reportPrivateUsage]
                )
            finally:
                probe._active = False  # pyright: ignore[reportPrivateUsage]
            return response

        monkeypatch.setattr(CommandBus, "dispatch", dispatch)

    @staticmethod
    async def _repeat(
        bus: CommandBus,
        original: Callable[[CommandBus, JsonRpcRequest], Awaitable[JsonRpcResponse]],
        request: JsonRpcRequest,
        change: Callable[[str, dict[str, Any]], None] | None,
    ) -> Repeat:
        engine = bus._operations._engine  # pyright: ignore[reportPrivateUsage,reportAttributeAccessIssue,reportUnknownMemberType,reportUnknownVariableType]
        database = str(engine.url.database)  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType,reportUnknownArgumentType]
        before = domain_snapshot(engine)  # pyright: ignore[reportUnknownArgumentType]
        saved = sqlite3.connect(":memory:")
        with closing(sqlite3.connect(database)) as live:
            live.backup(saved)
        raw = request.model_dump(mode="json", by_alias=True)
        key = request.params.meta.idempotency_key + "#repeat"
        raw["id"] = key
        raw["params"]["_meta"]["idempotencyKey"] = key
        if change is not None:
            change(request.method, raw["params"]["input"])
        outcome = await original(bus, JsonRpcRequest.model_validate(raw))
        after = domain_snapshot(engine)  # pyright: ignore[reportUnknownArgumentType]
        notifications: tuple[str, ...] = ()
        unchanged_reason: str | None = None
        cancelled: object = None
        if outcome.result is not None:
            answered = outcome.result.get("value")
            if isinstance(answered, dict):
                if answered.get("unchanged") is True:
                    unchanged_reason = str(answered.get("unchanged_reason"))
                cancelled = answered.get("cancelled")
            with closing(sqlite3.connect(database)) as live:
                rows = live.execute(
                    "SELECT event_type FROM events WHERE operation_id = ? "
                    "ORDER BY created_at, event_id",
                    (str(outcome.result["operation_id"]),),
                ).fetchall()
            notifications = tuple(
                str(row[0]) for row in rows if not str(row[0]).startswith("operation.")
            )
        with closing(sqlite3.connect(database)) as live:
            saved.backup(live)  # the probe leaves no trace in the flow under test
        saved.close()
        changed = frozenset(name for name in after if after[name] != before.get(name))
        return Repeat(
            method=request.method,
            accepted=outcome.error is None,
            error_message=None if outcome.error is None else str(outcome.error.message),
            changed_tables=changed,
            added_rows={
                name: len(after[name]) - len(before.get(name, ()))
                for name in sorted(changed)
                if len(after[name]) != len(before.get(name, ()))
            },
            notifications=notifications,
            unchanged_reason=unchanged_reason,
            cancelled=cancelled,
        )
