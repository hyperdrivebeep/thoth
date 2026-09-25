"""Whole-DB snapshots with explicit row-level allowances, never ignored tables."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Engine, select, text

from thoth.adapters.storage.resource_scope_schema import operation_resource_bindings
from thoth.adapters.storage.schema import events, metadata, operations
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.resource_scope import OperationResourceBinding
from thoth.protocol.jsonrpc import JsonRpcRequest

Snapshot = dict[str, tuple[str, ...]]


def snapshot(engine: Engine) -> Snapshot:
    with engine.connect() as connection:
        result = {
            name: tuple(
                sorted(
                    json.dumps(dict(row), sort_keys=True, default=str)
                    for row in connection.execute(select(table)).mappings()
                )
            )
            for name, table in sorted(metadata.tables.items())
        }
        result["structural_fts"] = tuple(
            sorted(
                json.dumps(dict(row), sort_keys=True)
                for row in connection.execute(
                    text("SELECT node_id,project_id,artifact_id,text FROM structural_fts")
                ).mappings()
            )
        )
        return result


@dataclass(frozen=True)
class AllowedRow:
    table: str
    identity: dict[str, Any]
    direction: str
    states: dict[str, tuple[Any, ...]]

    def matches(self, table: str, direction: str, row: dict[str, Any]) -> bool:
        return (
            bool(self.identity)
            and self.table == table
            and self.direction == direction
            and all(row.get(key) == value for key, value in self.identity.items())
            and all(row.get(key) in values for key, values in self.states.items())
        )


def assert_phase_delta(
    before: Snapshot, after: Snapshot, allowed: tuple[AllowedRow, ...] = ()
) -> None:
    unexpected: list[tuple[str, str, str]] = []
    for table in sorted(before.keys() | after.keys()):
        left, right = Counter(before.get(table, ())), Counter(after.get(table, ()))
        for direction, changes in (("removed", left - right), ("added", right - left)):
            for serialized in changes.elements():
                row = json.loads(serialized)
                if not any(rule.matches(table, direction, row) for rule in allowed):
                    unexpected.append((table, direction, serialized))
    assert not unexpected, unexpected


def failed_command_allowances(
    engine: Engine, command: JsonRpcRequest, *, expected_reads: tuple[str, ...] = ()
) -> tuple[AllowedRow, ...]:
    project = str(command.params.input["project_id"])
    with engine.connect() as connection:
        operation = dict(
            connection.execute(
                select(operations).where(
                    operations.c.project_id == project,
                    operations.c.method == command.method,
                    operations.c.idempotency_key == command.params.meta.idempotency_key,
                )
            )
            .mappings()
            .one()
        )
        identifier = operation["operation_id"]
        binding_row = dict(
            connection.execute(
                select(operation_resource_bindings).where(
                    operation_resource_bindings.c.operation_id == identifier,
                )
            )
            .mappings()
            .one()
        )
        observed_events = (
            connection.execute(
                select(events.c.event_type).where(
                    events.c.operation_id == identifier,
                )
            )
            .scalars()
            .all()
        )
    scope = domain_digest(
        "RPC_SCOPE",
        "1.0.0",
        canonical_payload(
            {
                "method": command.method,
                "input": command.params.input,
                "expected_head_digest": command.params.meta.expected_head_digest,
                "field_session_id": command.params.meta.field_session_id,
            }
        ),
    )
    assert operation["state"] == "FAILED" and operation["result_json"] is None
    assert operation["scope_digest"] == scope and operation["completed_at"] is not None
    assert sorted(observed_events) == ["operation.failed", "operation.started"]
    binding = OperationResourceBinding.model_validate_json(binding_row["content_json"])
    assert binding.operation_id == identifier and binding.project_id == project
    assert binding.request_digest == scope and binding.output_kind == "ERROR"
    assert binding.output_digest == domain_digest(
        "OPERATION_RESOURCE_OUTPUT", "1.0.0", canonical_payload(json.loads(operation["error_json"]))
    )
    assert sorted((use.resource_ref, use.capability) for use in binding.resource_uses) == sorted(
        (reference, "READ") for reference in expected_reads
    )
    assert binding_row["binding_digest"] == binding.binding_digest
    identity = {"operation_id": identifier, "project_id": project}
    return (
        AllowedRow(
            "operations",
            {
                **identity,
                "method": command.method,
                "idempotency_key": command.params.meta.idempotency_key,
                "scope_digest": scope,
            },
            "added",
            {"state": ("FAILED",), "result_json": (None,)},
        ),
        AllowedRow(
            "idempotency_keys",
            {
                **identity,
                "method": command.method,
                "idempotency_key": command.params.meta.idempotency_key,
                "scope_digest": scope,
            },
            "added",
            {},
        ),
        AllowedRow(
            "events", identity, "added", {"event_type": ("operation.failed", "operation.started")}
        ),
        AllowedRow("operation_resource_bindings", binding_row, "added", {}),
    )
