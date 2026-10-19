"""D1 accepted creation checkpoint faults, separate from behavior-preserving refactors."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from tests.atomicity.harness import assert_phase_delta, failed_command_allowances, snapshot
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_a03_storage_atomicity import setup

from thoth.adapters.storage.decision_object import SqliteDecisionObjectStore
from thoth.adapters.storage.dependency import SqliteDependencyGraph
from thoth.adapters.storage.schema import metadata
from thoth.adapters.storage.sqlite import SqliteLedgerTransaction
from thoth.adapters.storage.threads import SqliteThreadStore
from thoth.adapters.storage.transaction import read_connection
from thoth.protocol.deferred import current_operation
from thoth.protocol.jsonrpc import RpcErrorCode

OUT = Path(__file__).resolve().parents[2] / "outputs/defect-d1-object-followup-20261014"
THREAD = "thread:a03-storage"
MATERIALIZE_POINTS = (
    "add_candidate",
    "insert_snapshot",
    "insert_revision",
    "set_head",
    "insert_receipt",
    "add_object",
    "append_audit",
)
FOLLOWUP_POINTS = (
    "before_relation",
    "add_relation",
    "dependency",
    "second_receipt",
    "thread_cas_reject",
    "after_thread_write",
)
DOMAIN_TABLES = (
    "object_candidates",
    "decision_objects",
    "object_relations",
    "relations",
    "entity_snapshots",
    "semantic_revisions",
    "working_heads",
    "receipts",
    "threads",
)


def save(name: str, data: dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / (name + ".json")).write_text(
        json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8"
    )


async def prepare(tmp_path: Path, kind: str, key: str) -> tuple[Any, dict[str, Any], Any]:
    runtime, ctx = await setup(tmp_path)
    parent = value(
        await runtime.bus.dispatch(
            request(
                "object/read",
                key + "-parent",
                {"project_id": ctx["project"], "object_id": ctx["object"]},
            )
        )
    )["object"]
    purpose = "D1 independent followup " + key
    payload: dict[str, Any]
    if kind == "materialize":
        payload = {
            "project_id": ctx["project"],
            "thread_id": THREAD,
            "purpose_statement": purpose,
            "focus_refs": ctx["spans"],
            "trigger_evidence_refs": ctx["spans"],
            "profile_refs": ["GENERAL_RND_DECISION"],
        }
        method = "object/materialize"
    else:
        payload = {
            "project_id": ctx["project"],
            "object_id": ctx["object"],
            "expected_revision_digest": parent["revision_digest"],
            "purpose_statement": purpose,
            "trigger_refs": ctx["spans"],
            "inherit_scope": True,
        }
        method = "object/followup/create"
    ctx.update(parent=parent, purpose=purpose, payload=payload, method=method)
    return runtime, ctx, request(method, key, payload)


@pytest.mark.parametrize(
    "kind,point",
    [
        *[("materialize", p) for p in MATERIALIZE_POINTS],
        *[("followup", p) for p in FOLLOWUP_POINTS],
    ],
)
async def test_creation_fault_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, point: str
) -> None:
    key = "d1-" + kind + "-" + point
    runtime, ctx, command = await prepare(tmp_path, kind, key)
    before = snapshot(runtime.ledger.engine)
    project = ctx["project"]
    state: dict[str, Any] = {"child": None, "revisions": {}}
    hits: list[dict[str, Any]] = []
    seams = [
        (SqliteDecisionObjectStore, "add_candidate"),
        (SqliteLedgerTransaction, "insert_snapshot"),
        (SqliteLedgerTransaction, "insert_revision"),
        (SqliteLedgerTransaction, "set_head"),
        (SqliteLedgerTransaction, "insert_receipt"),
        (SqliteDecisionObjectStore, "add_object"),
        (SqliteDecisionObjectStore, "append_audit"),
        (SqliteDecisionObjectStore, "add_relation"),
        (SqliteDependencyGraph, "add"),
        (SqliteThreadStore, "update"),
    ]

    def observe_fault(name: str, record: Any, args: tuple[Any, ...]) -> None:
        if name == "set_head":
            table_name, identity = (
                "working_heads",
                {"project_id": project, "aggregate_key": args[1], "revision_digest": args[2]},
            )
        elif name == "update":
            table_name, identity = "decision_objects", {"object_id": state["child"]}
        else:
            table_name, field = {
                "add_candidate": ("object_candidates", "candidate_id"),
                "insert_snapshot": ("entity_snapshots", "snapshot_id"),
                "insert_revision": ("semantic_revisions", "revision_id"),
                "insert_receipt": ("receipts", "receipt_id"),
                "add_object": ("decision_objects", "object_revision_id"),
                "append_audit": ("object_audit", "audit_id"),
                "add_relation": ("object_relations", "relation_revision_id"),
                "add": ("relations", "relation_id"),
            }[name]
            identity = {"project_id": project, field: getattr(record, field)}
        if point == "before_relation":
            table_name, identity = "decision_objects", {"object_id": record.source_object_id}
        table = metadata.tables[table_name]
        with read_connection(runtime.ledger.engine) as connection:
            assert connection.in_transaction()
            rows = list(
                connection.execute(
                    select(table).where(*(table.c[k] == v for k, v in identity.items()))
                ).mappings()
            )
            assert rows, (table_name, identity)
            if point == "after_thread_write":
                thread_table = metadata.tables["threads"]
                staged_thread = (
                    connection.execute(
                        select(thread_table).where(thread_table.c.thread_id == THREAD)
                    )
                    .mappings()
                    .one()
                )
                assert staged_thread["revision"] == record.revision
            external = snapshot(runtime.ledger.engine)
            for table_key in DOMAIN_TABLES:
                assert external[table_key] == before[table_key], table_key
            hits.append(
                {
                    "point": point,
                    "identity": identity,
                    "staged_rows": [dict(r) for r in rows],
                    "child": state["child"],
                    "revisions": dict(state["revisions"]),
                    "external_domain_unchanged": True,
                    "connection": id(connection),
                }
            )

    def instrument(name: str, original: Any) -> Any:
        def wrapped(instance: Any, *args: Any, **kwargs: Any) -> Any:
            operation = current_operation.get()
            active = (
                operation is not None
                and operation.project_id == project
                and operation.idempotency_key == key
            )
            if not active:
                return original(instance, *args, **kwargs)
            record = args[0]
            matching = False
            if name == "add_candidate":
                matching = (
                    record.project_id == project and record.purpose_statement == ctx["purpose"]
                )
            elif name == "insert_snapshot":
                matching = (
                    record.project_id == project
                    and record.entity_type.value == "DECISION_OBJECT"
                    and record.content.get("purpose_statement") == ctx["purpose"]
                )
                if matching:
                    state["child"] = record.entity_id
                    if kind == "followup":
                        assert record.content["parent_object_id"] == ctx["object"]
            elif name == "insert_revision":
                matching = record.project_id == project and record.entity_id == state["child"]
                if matching:
                    state["revisions"][record.revision_id] = record.reason
            elif name == "set_head":
                matching = args[0] == project and args[1] == "DECISION_OBJECT:" + str(
                    state["child"]
                )
            elif name == "insert_receipt":
                matching = record.project_id == project and bool(
                    set(record.subject_refs) & state["revisions"].keys()
                )
            elif name in {"add_object", "append_audit"}:
                matching = record.project_id == project and record.object_id == state["child"]
            elif name == "add_relation":
                matching = (
                    record.project_id == project
                    and record.source_object_id == state["child"]
                    and record.target_ref == "OBJECT:" + ctx["object"]
                    and record.relation_type == "FOLLOWUP_OF"
                )
            elif name == "add":
                matching = (
                    record.project_id == project
                    and record.source_ref == "OBJECT:" + str(state["child"])
                    and record.target_ref == "OBJECT:" + ctx["object"]
                    and record.relation_type == "FOLLOWUP_OF"
                )
            elif name == "update":
                matching = (
                    record.thread_id == THREAD and state["child"] in record.current_object_ids
                )
            target = matching and (
                (kind == "materialize" and name == point)
                or (
                    kind == "followup"
                    and (
                        (name == "add_relation" and point in {"before_relation", "add_relation"})
                        or (name == "add" and point == "dependency")
                        or (
                            name == "insert_receipt"
                            and point == "second_receipt"
                            and any(
                                state["revisions"].get(ref) == "object/relationChanged"
                                for ref in record.subject_refs
                            )
                        )
                        or (
                            name == "update"
                            and point in {"thread_cas_reject", "after_thread_write"}
                        )
                    )
                )
            )
            if target and point in {"before_relation", "thread_cas_reject"}:
                observe_fault(name, record, args)
                if point == "thread_cas_reject":
                    return False
                raise RuntimeError("D1 before relation fault")
            result = original(instance, *args, **kwargs)
            if target:
                observe_fault(name, record, args)
                raise RuntimeError("D1 post-write fault: " + point)
            return result

        return wrapped

    try:
        with monkeypatch.context() as patch:
            for owner, name in seams:
                patch.setattr(owner, name, instrument(name, getattr(owner, name)))
            response = await runtime.bus.dispatch(command)
        after = snapshot(runtime.ledger.engine)
        save(
            key,
            {
                "response": response.model_dump(mode="json"),
                "hits": hits,
                "before": before,
                "after": after,
            },
        )
        assert len(hits) == 1 and response.error is not None
        if point == "thread_cas_reject":
            assert response.error.code == RpcErrorCode.DOMAIN_REJECTED
            assert response.error.message == "thread revision changed while attaching Object"
        allowed = failed_command_allowances(
            runtime.ledger.engine,
            command,
        )
        assert_phase_delta(before, after, allowed)
        assert {table for table in before if before[table] != after[table]} == {
            "operations",
            "idempotency_keys",
            "events",
            "operation_resource_bindings",
        } | (
            {"operation_owners"}
            if before["operation_owners"] != after["operation_owners"]
            else set()
        )
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path)
    try:
        assert snapshot(reopened.ledger.engine) == after
        replay = await reopened.bus.dispatch(command)
        assert replay.error == response.error
        assert snapshot(reopened.ledger.engine) == after
        parent = value(
            await reopened.bus.dispatch(
                request(
                    "object/read",
                    key + "-parent-after",
                    {"project_id": project, "object_id": ctx["object"]},
                )
            )
        )["object"]
        assert parent == ctx["parent"]
        recovered = value(
            await reopened.bus.dispatch(request(ctx["method"], key + "-retry", ctx["payload"]))
        )
        thread = value(
            await reopened.bus.query(
                request(
                    "thread/read", key + "-thread", {"project_id": project, "thread_id": THREAD}
                )
            )
        )
        child = recovered["object"]
        assert child["object_id"] in thread["current_object_ids"]
        assert (
            reopened.ledger.read_heads(project)["DECISION_OBJECT:" + child["object_id"]]
            == child["revision_digest"]
        )
        if kind == "followup":
            assert child["parent_object_id"] == ctx["object"]
            assert recovered["lineage"]["target_ref"] == "OBJECT:" + ctx["object"]
        save(
            key,
            {
                "verdict": "PASS",
                "request": command.model_dump(mode="json"),
                "response": response.model_dump(mode="json"),
                "hits": hits,
                "before": before,
                "after": after,
                "reopened_exact": True,
                "replay_exact": True,
                "parent_unchanged": True,
                "recovered": recovered,
            },
        )
    finally:
        reopened.close()


@pytest.mark.parametrize("case", ["stale", "hold"])
async def test_followup_stale_and_hold_preserve_existing_state(tmp_path: Path, case: str) -> None:
    runtime, ctx, _ = await prepare(tmp_path, "followup", "d1-" + case)
    payload = dict(ctx["payload"])
    payload.update(
        {"expected_revision_digest": "0" * 64} if case == "stale" else {"trigger_refs": []}
    )
    before = snapshot(runtime.ledger.engine)
    try:
        response = await runtime.bus.dispatch(request(ctx["method"], "d1-" + case, payload))
        after = snapshot(runtime.ledger.engine)
        for table in DOMAIN_TABLES:
            if case == "hold" and table == "object_candidates":
                assert len(after[table]) == len(before[table]) + 1
            else:
                assert after[table] == before[table], table
        if case == "stale":
            assert response.error is not None
            assert response.error.message == "object revision changed concurrently"
        else:
            result = value(response)
            assert result["object"] is None and result["commit"] is None
            assert result["candidate"]["permitted_transition"] == "HOLD_ENTRY_INVALID"
        save(
            "d1-" + case,
            {"response": response.model_dump(mode="json"), "before": before, "after": after},
        )
    finally:
        runtime.close()
