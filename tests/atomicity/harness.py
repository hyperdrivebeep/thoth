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
from thoth.domain.operation import OperationOwnerRecord
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
    allowed = (*allowed, *operation_owner_allowances(before, after, allowed))
    unexpected: list[tuple[str, str, str]] = []
    for table in sorted(before.keys() | after.keys()):
        left, right = Counter(before.get(table, ())), Counter(after.get(table, ()))
        for direction, changes in (("removed", left - right), ("added", right - left)):
            for serialized in changes.elements():
                row = json.loads(serialized)
                if not any(rule.matches(table, direction, row) for rule in allowed):
                    unexpected.append((table, direction, serialized))
    assert not unexpected, unexpected


def operation_owner_allowances(
    before: Snapshot, after: Snapshot, allowed: tuple[AllowedRow, ...]
) -> tuple[AllowedRow, ...]:
    """At most one owner added for each explicitly allowed, newly created operation."""
    old_ids = {json.loads(row)["operation_id"] for row in before.get("operations", ())}
    old_owner_ids = {json.loads(row)["operation_id"] for row in before.get("operation_owners", ())}
    new_operations = Counter(after.get("operations", ())) - Counter(before.get("operations", ()))
    owners = [json.loads(row) for row in after.get("operation_owners", ())]
    result: list[AllowedRow] = []
    for raw in new_operations:
        operation = json.loads(raw)
        identifier = operation["operation_id"]
        if identifier in old_ids or identifier in old_owner_ids:
            continue
        if not any(
            rule.identity.get("operation_id") == identifier
            and rule.matches("operations", "added", operation)
            for rule in allowed
        ):
            continue
        rows = [row for row in owners if row["operation_id"] == identifier]
        assert len(rows) <= 1, "duplicate owner rows for one operation"
        if rows:
            row = rows[0]
            owner = OperationOwnerRecord.model_validate(
                {key: value for key, value in row.items() if key != "operation_id"}
            )
            assert owner.host_token is not None
            result.append(AllowedRow("operation_owners", row, "added", {}))
    return tuple(result)


def unchanged_or_operation_owner(
    before: Snapshot, after: Snapshot, table: str, command: JsonRpcRequest
) -> bool:
    """Keep domain equality exact; validate only this new request's companion owner."""
    if before.get(table, ()) == after.get(table, ()):
        return True
    if table != "operation_owners":
        return False
    candidates = [
        json.loads(row)
        for row in after.get("operations", ())
        if json.loads(row)["method"] == command.method
        and json.loads(row)["idempotency_key"] == command.params.meta.idempotency_key
        and json.loads(row)["project_id"] == command.params.input["project_id"]
    ]
    assert len(candidates) == 1
    operation = candidates[0]
    allowed = operation_owner_allowances(
        before, after, (AllowedRow("operations", operation, "added", {}),)
    )
    left, right = Counter(before.get(table, ())), Counter(after.get(table, ()))
    assert not left - right, "owner removal or replacement is not bookkeeping"
    added = list((right - left).elements())
    assert len(added) == 1 and len(allowed) == 1, "expected exactly this request's owner row"
    assert allowed[0].matches(table, "added", json.loads(added[0]))
    return True


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
