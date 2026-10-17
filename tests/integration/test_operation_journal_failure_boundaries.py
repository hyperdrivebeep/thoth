"""Observe generic bus journal failures without changing claim or terminal ownership."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from itertools import pairwise
from pathlib import Path
from typing import Any, Literal

import pytest
from sqlalchemy import Connection, create_engine, event, select
from tests.atomicity.harness import Snapshot, snapshot
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import request, value

from thoth.adapters.storage import SqliteProjectStore
from thoth.adapters.storage.events import SqliteEventStore
from thoth.adapters.storage.schema import idempotency_keys, operations
from thoth.apps.runtime import AppRuntime
from thoth.domain.event import Checkpoint
from thoth.ports.model import ModelPort
from thoth.protocol.jsonrpc import JsonRpcRequest, JsonRpcResponse

PROJECT = "project:journal-boundary"
Mode = Literal["control", "started", "checkpoint"]


class InjectedJournalFailure(RuntimeError):
    pass


class NoModels:
    def __init__(self) -> None:
        self.calls = 0

    def resolve(self, *, provider: str, model: str | None) -> ModelPort:
        self.calls += 1
        raise AssertionError("MODEL_RESOLUTION_FORBIDDEN_IN_JOURNAL_TEST")


def rows(state: Snapshot, table: str) -> list[dict[str, Any]]:
    return [json.loads(row) for row in state[table]]


def operation_row(state: Snapshot, command: JsonRpcRequest) -> dict[str, Any]:
    found = [
        row
        for row in rows(state, "operations")
        if row["project_id"] == PROJECT
        and row["method"] == command.method
        and row["idempotency_key"] == command.params.meta.idempotency_key
    ]
    assert len(found) == 1
    return found[0]


def independent_snapshot(runtime: AppRuntime) -> Snapshot:
    engine = create_engine(runtime.ledger.engine.url)
    try:
        return snapshot(engine)
    finally:
        engine.dispose()


def revision(runtime: AppRuntime) -> int:
    project = SqliteProjectStore(runtime.ledger.engine).read(PROJECT)
    assert project is not None
    return project.revision


def journal_status(state: Snapshot, identifier: str) -> dict[str, object]:
    types = sorted(
        row["event_type"] for row in rows(state, "events") if row["operation_id"] == identifier
    )
    checkpoint_count = sum(row["operation_id"] == identifier for row in rows(state, "checkpoints"))
    missing = [
        name
        for name in ("operation.started", "project/metadata/updated", "operation.succeeded")
        if name not in types
    ]
    if checkpoint_count != 1:
        missing.append("checkpoint")
    return {"event_types": types, "checkpoint_count": checkpoint_count, "missing": missing}


def snapshot_summary(state: Snapshot) -> dict[str, object]:
    return {
        "tables": len(state),
        "rows": {name: len(data) for name, data in state.items()},
        "sha256": hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest(),
    }


def retry_acceptable(response: JsonRpcResponse, observed_revision: int) -> bool:
    if response.error is not None:
        return response.error.data.get("retryable") is True
    return (
        observed_revision == 1
        and response.result is not None
        and response.result.get("state") == "SUCCEEDED"
    )


async def exercise(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    record_property: Callable[[str, object], None],
    mode: Mode,
) -> None:
    workspace = tmp_path / "workspace"
    model = NoModels()
    runtime = create_runtime(workspace, model_resolver=model)
    command = request(
        "project/metadata/update",
        "journal-boundary-update",
        {"project_id": PROJECT, "expected_revision": 0, "name": "Changed exactly once"},
    )
    hits: list[Snapshot] = []
    staged_claims: list[str] = []
    initial: dict[str, object] = {}
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "journal-boundary-create",
                    {
                        "project_id": PROJECT,
                        "name": "Journal boundary",
                        "cutoff_at": "2026-09-05T00:00:00Z",
                    },
                )
            )
        )
        assert revision(runtime) == 0
        before = snapshot(runtime.ledger.engine)
        insert_checkpoint = SqliteEventStore.insert_checkpoint

        def fail_started(
            connection: Connection,
            cursor: Any,
            statement: str,
            parameters: Any,
            context: Any,
            many: bool,
        ) -> None:
            del cursor, parameters, context, many
            if not statement.startswith("INSERT INTO events"):
                return
            identifier = connection.execute(
                select(operations.c.operation_id).where(
                    operations.c.idempotency_key == command.params.meta.idempotency_key
                )
            ).scalar_one()
            assert (
                connection.execute(
                    select(idempotency_keys.c.operation_id).where(
                        idempotency_keys.c.operation_id == identifier
                    )
                ).scalar_one()
                == identifier
            )
            staged_claims.append(str(identifier))
            hits.append(independent_snapshot(runtime))
            raise InjectedJournalFailure("STARTED_INSERT_BLOCKED_INSIDE_CLAIM")

        def fail_checkpoint(store: SqliteEventStore, checkpoint: Checkpoint) -> None:
            state = independent_snapshot(runtime)
            target = operation_row(state, command)
            if checkpoint.operation_id == target["operation_id"]:
                hits.append(state)
                raise InjectedJournalFailure("CHECKPOINT_INSERT_BLOCKED_AFTER_COMPLETE")
            insert_checkpoint(store, checkpoint)

        with monkeypatch.context() as patch:
            if mode == "started":
                event.listen(runtime.ledger.engine, "before_cursor_execute", fail_started)
            elif mode == "checkpoint":
                patch.setattr(SqliteEventStore, "insert_checkpoint", fail_checkpoint)
            try:
                response = await runtime.bus.dispatch(command)
                initial = {"kind": "response", "value": response.model_dump(mode="json")}
            except InjectedJournalFailure as exc:
                initial = {"kind": "exception", "type": type(exc).__name__, "message": str(exc)}
            finally:
                if mode == "started":
                    event.remove(runtime.ledger.engine, "before_cursor_execute", fail_started)

        after = snapshot(runtime.ledger.engine)
        assert after == independent_snapshot(runtime)
        assert len(hits) == (0 if mode == "control" else 1), (
            "NOT_TRIGGERED or repeated journal fault"
        )
        if hits:
            assert hits[0] == after
            assert initial["kind"] == "exception"
        if mode == "started":
            assert len(staged_claims) == 1, "NOT_TRIGGERED: started INSERT was not reached"
            assert hits[0] == before == after
            initial_state = "ABSENT_AFTER_ROLLBACK"
            assert revision(runtime) == 0
        else:
            operation = operation_row(after, command)
            identifier = str(operation["operation_id"])
            initial_state = str(operation["state"])
            assert operation["state"] == "SUCCEEDED"
            assert revision(runtime) == 1
            assert (
                len(
                    [
                        row
                        for row in rows(after, "operation_resource_bindings")
                        if row["operation_id"] == identifier
                    ]
                )
                == 1
            )
            sealed = runtime.bus.read_operation(identifier)
            assert sealed is not None and sealed.result is not None
            assert sealed.result["revision"] == 1
            if mode == "control":
                journal = SqliteEventStore(runtime.ledger.engine)
                chain = journal.list_events(identifier)
                assert [event.event_type for event in chain] == [
                    "operation.started",
                    "project/metadata/updated",
                    "operation.succeeded",
                ]
                assert chain[0].previous_event_digest is None
                for previous, following in pairwise(chain):
                    assert following.previous_event_digest == previous.event_digest
                checkpoint = journal.latest_checkpoint(identifier)
                assert checkpoint is not None
                assert checkpoint.payload["result_digest"] == chain[-1].payload["result_digest"]
        live_tasks = runtime.bus.has_running_research_tasks()
        same_runtime = await runtime.bus.dispatch(command)
        same_revision = revision(runtime)
        retried = snapshot(runtime.ledger.engine)
        operation = operation_row(retried, command)
        identifier = str(operation["operation_id"])
        assert (
            len(
                [
                    row
                    for row in rows(retried, "idempotency_keys")
                    if row["operation_id"] == identifier
                ]
            )
            == 1
        )
    finally:
        runtime.close()

    reopened = create_runtime(workspace, model_resolver=model)
    try:
        reopened_before = snapshot(reopened.ledger.engine)
        assert reopened_before == retried
        after_reopen = await reopened.bus.dispatch(command)
        reopened_revision = revision(reopened)
        final = snapshot(reopened.ledger.engine)
        assert final == retried
        assert final == independent_snapshot(reopened)
        reopened_tasks = reopened.bus.has_running_research_tasks()
    finally:
        reopened.close()

    assert model.calls == 0
    evidence = {
        "mode": mode,
        "fault_hits": len(hits),
        "model_resolutions": model.calls,
        "initial": initial,
        "operation_id": identifier,
        "initial_operation_state": initial_state,
        "staged_claims": staged_claims,
        "same_runtime_response": same_runtime.model_dump(mode="json"),
        "reopened_response": after_reopen.model_dump(mode="json"),
        "revisions": [same_revision, reopened_revision],
        "running_tasks": [live_tasks, reopened_tasks],
        "same_runtime_acceptable": retry_acceptable(same_runtime, same_revision),
        "reopened_acceptable": retry_acceptable(after_reopen, reopened_revision),
        "journal_after_failure": journal_status(after, identifier),
        "journal_after_reopen_retry": journal_status(final, identifier),
        "no_writes_on_retries": after == retried == final,
        "snapshots": {
            "before": snapshot_summary(before),
            "after": snapshot_summary(after),
            "retried": snapshot_summary(retried),
            "reopened": snapshot_summary(final),
        },
        "changed_tables": [name for name in before if before[name] != after[name]],
        "journal_disposition": (
            "OBSERVED_POST_COMPLETION_GAP"
            if mode == "checkpoint" and journal_status(final, identifier)["missing"]
            else "OBSERVED"
        ),
    }
    evidence_path = tmp_path / f"{mode}-observation.json"
    evidence_path.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    record_property("evidence_path", str(evidence_path))

    if mode == "started":
        assert evidence["same_runtime_acceptable"] and evidence["reopened_acceptable"], (
            "HOLD: post-claim failure must permit one domain mutation or a typed retryable error; "
            f"observed {same_runtime.model_dump(mode='json')} / "
            f"{after_reopen.model_dump(mode='json')}, "
            f"revisions={same_revision, reopened_revision}, "
            f"running_tasks={live_tasks, reopened_tasks}"
        )
    else:
        assert after == retried == final
        assert same_runtime.result == after_reopen.result
        stored_result = operation["result_json"]
        assert isinstance(stored_result, str)
        assert value(same_runtime) == json.loads(stored_result)
        assert same_revision == reopened_revision == 1
        if mode == "control":
            assert initial["value"] == same_runtime.model_dump(mode="json")
            assert journal_status(final, identifier)["missing"] == []


async def test_normal_command_replays_once_with_complete_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, record_property: Callable[[str, object], None]
) -> None:
    await exercise(tmp_path, monkeypatch, record_property, "control")


async def test_post_claim_journal_failure_remains_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, record_property: Callable[[str, object], None]
) -> None:
    await exercise(tmp_path, monkeypatch, record_property, "started")


async def test_post_complete_journal_failure_preserves_result_without_reexecution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, record_property: Callable[[str, object], None]
) -> None:
    await exercise(tmp_path, monkeypatch, record_property, "checkpoint")
