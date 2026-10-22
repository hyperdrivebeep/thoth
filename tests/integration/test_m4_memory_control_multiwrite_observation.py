"""Two-write MEMORY observations, updated to the approved P1 atomic-pair contract.

memory/validate (GATE then CANDIDATE revision), memory/changeSet/propose
(MEMORY_ENTRY_CANDIDATE then CHANGE_SET_PROPOSAL) and memory/retire/propose (RETIRE_CANDIDATE then
CHANGE_SET_PROPOSAL) call ControlRecordService.create twice. P1 wraps each pair in the handler's
short ledger transaction; the handler awaits nothing between these writes.

The second append of the target request is made to fail with an injected error, either before it
writes ("before") or after it wrote ("after"). Both now roll back the whole pair. Before P1, these
faults left one or two committed rows; the V-P1 evidence retains that baseline. The real runtime
checks all tables, the failed operation, same-key replay, runtime reopening, new-key retry,
and later reads. validate also gets one simulated competing
writer (a second revision of the same record between the read and the append), which is what another
process could previously do; this nested injection does not prove cross-process concurrency safety.

Evidence files: outputs/refactor-step96-20261015/step0-multiwrite-*.json.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from tests.atomicity.harness import snapshot, unchanged_or_operation_owner
from tests.integration.storage_coverage_helpers import request, value

from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.storage.control_record import SqliteControlRecordStore
from thoth.adapters.storage.schema import control_records
from thoth.application.services.control_record_service import ControlRecordService
from thoth.apps.runtime import create_runtime
from thoth.protocol.deferred import current_operation

OUT = Path(__file__).resolve().parents[2] / "outputs/refactor-step96-20261015"
PROJECT = "project:memory-step0"
BOOKKEEPING = {
    "operations",
    "idempotency_keys",
    "events",
    "operation_resource_bindings",
    "checkpoints",
}
ROUTES = ("validate", "change_set_propose", "retire_propose")
METHODS = {
    "validate": "memory/validate",
    "change_set_propose": "memory/changeSet/propose",
    "retire_propose": "memory/retire/propose",
}
# Source-derived order of the two appends for each route (memory_full.py).
EXPECTED_ORDER = {
    "validate": (("GATE", "validate-gate"), ("CANDIDATE", "validate-candidate")),
    "change_set_propose": (
        ("MEMORY_ENTRY_CANDIDATE", "entry"),
        ("CHANGE_SET_PROPOSAL", "change-set"),
    ),
    "retire_propose": (("RETIRE_CANDIDATE", "retire"), ("CHANGE_SET_PROPOSAL", "change-set")),
}


def save(name: str, data: dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"step0-multiwrite-{name}.json").write_text(
        json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8"
    )


async def call(runtime: Any, method: str, key: str, payload: dict[str, Any]) -> dict[str, Any]:
    return value(await runtime.bus.dispatch(request(method, key, payload)))


def rows(runtime: Any) -> list[dict[str, Any]]:
    with runtime.ledger.engine.connect() as connection:
        found = connection.execute(
            select(control_records)
            .where(control_records.c.project_id == PROJECT)
            .order_by(control_records.c.created_at, control_records.c.version)
        ).mappings()
        return [
            {
                "type": row["record_type"],
                "record_id": row["record_id"],
                "version": row["version"],
                "state": row["state"],
                "digest": row["record_digest"],
            }
            for row in found
        ]


def table_changes(before: Any, after: Any) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for table in sorted(before.keys() | after.keys()):
        left: Counter[str] = Counter(before.get(table, ()))
        right: Counter[str] = Counter(after.get(table, ()))
        added, removed = sum((right - left).values()), sum((left - right).values())
        if added or removed:
            result[table] = {"added": added, "removed": removed}
    return result


def domain_changes(
    changes: dict[str, dict[str, int]], before: Any, after: Any, command: Any
) -> dict[str, dict[str, int]]:
    if "operation_owners" in changes:
        assert unchanged_or_operation_owner(before, after, "operation_owners", command)
        changes = {name: delta for name, delta in changes.items() if name != "operation_owners"}
    return {name: delta for name, delta in changes.items() if name not in BOOKKEEPING}


async def prepare(path: Path, route: str) -> tuple[Any, str, dict[str, Any], str]:
    runtime = create_runtime(path)
    await call(
        runtime,
        "project/create",
        "step0-project",
        {"project_id": PROJECT, "name": "Memory step 0", "cutoff_at": "2026-08-31T00:00:00Z"},
    )
    if route == "retire_propose":
        await call(
            runtime,
            "thread/start",
            "step0-thread",
            {
                "project_id": PROJECT,
                "thread_id": "thread:memory-step0",
                "problem": "Recall only current project lessons",
                "scope": {"workstream": "integration"},
            },
        )
        owner_revision = next(iter(runtime.ledger.read_heads(PROJECT).values()))
        controls = ControlRecordService(
            store=SqliteControlRecordStore(runtime.ledger.engine),
            clock=SystemClock(),
            ids=UuidIdGenerator(),
        )
        entry = controls.create(
            project_id=PROJECT,
            namespace="MEMORY",
            record_type="MEMORY_ENTRY",
            state="COMMITTED",
            payload={
                "payload_mode": "MEMORY_ASSERTION",
                "recall_class": "LESSON",
                "assertion": "Check clock-source lineage before comparing runs",
                "domain_revision_ref": owner_revision,
                "scope": {"workstream": "integration"},
                "evidence_refs": (),
                "pipeline_status": "COMMITTED",
                "support_status": "SUPPORTED",
                "authority_status": "OBSERVED_SOURCE",
                "lifecycle_status": "ACTIVE",
                "recall_eligibility": "WORKING_CONTEXT",
                "classification_status": "CLEAR",
            },
        )
        return (
            runtime,
            METHODS[route],
            {
                "project_id": PROJECT,
                "memory_entry_id": entry.record_id,
                "reason": "replace with a newer scoped lesson",
                "evidence_refs": [],
                "expected_revision_digest": entry.record_digest,
            },
            entry.record_id,
        )
    created = await call(
        runtime,
        "memory/candidate/create",
        "step0-candidate",
        {
            "project_id": PROJECT,
            "producer_role": "Reflection",
            "payload_mode": "MEMORY_ASSERTION",
            "recall_class": "LESSON",
            "assertion_candidate": "Verify source versions before comparison",
            "parent_refs": [],
            "scope": {"workstream": "integration"},
            "evidence_refs": [],
            "risk_tags": [],
        },
    )
    candidate_id = str(created["candidate"]["record_id"])
    validate = {
        "project_id": PROJECT,
        "candidate_id": candidate_id,
        "transition_intent": "COMMIT_CANDIDATE",
        "policy_version": "memory:1",
    }
    if route == "validate":
        return runtime, METHODS[route], validate, candidate_id
    validated = await call(runtime, "memory/validate", "step0-validated", validate)
    return (
        runtime,
        METHODS[route],
        {
            "project_id": PROJECT,
            "candidate_id": candidate_id,
            "transition_intent": "COMMIT_CANDIDATE",
            "validated_gate_digest": validated["gate_digest"],
        },
        candidate_id,
    )


def two_write_fault(
    monkeypatch: pytest.MonkeyPatch, key: str, mode: str, hits: list[dict[str, Any]]
) -> None:
    original = SqliteControlRecordStore.append

    def append(self: SqliteControlRecordStore, record: Any) -> None:
        operation = current_operation.get()
        if operation is None or operation.idempotency_key != key:
            original(self, record)
            return
        hits.append({"type": record.record_type, "record_id": record.record_id})
        if len(hits) != 2:
            original(self, record)
            return
        if mode == "after":
            original(self, record)
        raise RuntimeError("step 0 injected second control write failure")

    monkeypatch.setattr(SqliteControlRecordStore, "append", append)


async def later_reads(
    runtime: Any, route: str, candidate_id: str, label: str = ""
) -> dict[str, Any]:
    """What a later request sees; read only."""

    if route == "retire_propose":
        listed = await call(
            runtime, "memory/list", f"step0-read-list-{label}", {"project_id": PROJECT}
        )
        return {"memory_entries": len(listed["memories"])}
    candidate = await call(
        runtime,
        "memory/candidate/read",
        f"step0-read-candidate-{route}-{label}",
        {"project_id": PROJECT, "candidate_id": candidate_id},
    )
    gates = await call(
        runtime,
        "memory/gate/read",
        f"step0-read-gates-{route}-{label}",
        {"project_id": PROJECT, "candidate_id": candidate_id},
    )
    return {
        "candidate_version": candidate["candidate"]["version"],
        "candidate_state": candidate["candidate"]["state"],
        "validated_gate_digest": candidate["candidate"]["payload"].get("validated_gate_digest"),
        "gates": [
            {"state": gate["state"], "gate_digest": gate["payload"]["gate_digest"]}
            for gate in gates["gates"]
        ],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
async def test_control_route_success_writes_the_two_rows_in_source_order(
    tmp_path: Path, route: str
) -> None:
    runtime, method, payload, candidate_id = await prepare(tmp_path / "workspace", route)
    try:
        before_rows = rows(runtime)
        before = snapshot(runtime.ledger.engine)
        await call(runtime, method, f"step0-{route}-ok", payload)
        added = rows(runtime)[len(before_rows) :]
        assert [(item["type"]) for item in added] == [t for t, _ in EXPECTED_ORDER[route]]
        after = snapshot(runtime.ledger.engine)
        assert set(
            domain_changes(
                table_changes(before, after),
                before,
                after,
                request(method, f"step0-{route}-ok", payload),
            )
        ) == {"control_records"}
        save(
            f"{route}-success",
            {
                "route": method,
                "rows_added_in_order": added,
                "later_reads": await later_reads(runtime, route, candidate_id, "ok"),
            },
        )
    finally:
        runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["before", "after"])
@pytest.mark.parametrize("route", ROUTES)
async def test_second_control_write_failure_rolls_back_both_rows_and_keeps_failed_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, route: str, mode: str
) -> None:
    workspace = tmp_path / "workspace"
    runtime, method, payload, candidate_id = await prepare(workspace, route)
    key = f"step0-{route}-{mode}"
    hits: list[dict[str, Any]] = []
    try:
        rows_before = rows(runtime)
        reads_before = await later_reads(runtime, route, candidate_id, "before")
        before = snapshot(runtime.ledger.engine)
        with monkeypatch.context() as patch:
            two_write_fault(patch, key, mode, hits)
            failed = await runtime.bus.dispatch(request(method, key, payload))
        assert failed.error is not None
        assert [item["type"] for item in hits] == [t for t, _ in EXPECTED_ORDER[route]]
        rows_after = rows(runtime)
        fault_after = snapshot(runtime.ledger.engine)
        changes = table_changes(before, fault_after)
        added = rows_after[len(rows_before) :]
        assert added == []
        assert rows_after == rows_before
        assert domain_changes(changes, before, fault_after, request(method, key, payload)) == {}
        reads_after_fault = await later_reads(runtime, route, candidate_id, "fault")
        runtime.close()

        runtime = create_runtime(workspace)
        assert rows(runtime) == rows_after
        before_replay = snapshot(runtime.ledger.engine)
        replayed = await runtime.bus.dispatch(request(method, key, payload))
        assert replayed.error is not None
        assert replayed.error == failed.error
        replay_changes = table_changes(before_replay, snapshot(runtime.ledger.engine))
        assert rows(runtime) == rows_after

        retry_key = key + "-new-key"
        retried = await call(runtime, method, retry_key, payload)
        rows_retry = rows(runtime)
        retry_added = rows_retry[len(rows_after) :]
        reads_after_retry = await later_reads(runtime, route, candidate_id, "retry")
        follow_up: dict[str, Any] | None = None
        if route == "validate":
            follow_up = await call(
                runtime,
                "memory/changeSet/propose",
                f"step0-{route}-{mode}-follow-up",
                {
                    "project_id": PROJECT,
                    "candidate_id": candidate_id,
                    "transition_intent": "COMMIT_CANDIDATE",
                    "validated_gate_digest": retried["gate_digest"],
                },
            )
        save(
            f"{route}-{mode}",
            {
                "route": method,
                "hits_in_order": hits,
                "error": failed.error.model_dump(mode="json"),
                "rows_added_by_failed_request": added,
                "domain_tables_changed_by_failed_request": domain_changes(
                    changes, before, fault_after, request(method, key, payload)
                ),
                "later_reads_before": reads_before,
                "later_reads_after_fault": reads_after_fault,
                "reopened_rows_equal": True,
                "same_key_replay_returns_same_error": True,
                "same_key_replay_changes": replay_changes,
                "new_key_retry_rows_added": retry_added,
                "later_reads_after_new_key_retry": reads_after_retry,
                "follow_up_changeSet_after_validate_retry": None
                if follow_up is None
                else sorted(follow_up),
            },
        )
        assert [item["type"] for item in retry_added] == [t for t, _ in EXPECTED_ORDER[route]]
        if route == "validate":
            # Approved P1 contract: neither gate nor candidate revision leaks on either fault.
            assert reads_after_fault == reads_before
            assert reads_after_fault["candidate_state"] == "CANDIDATE"
            assert reads_after_fault["gates"] == []
            assert reads_after_retry["candidate_state"] == "VALIDATED"
            assert len(reads_after_retry["gates"]) == 1
            assert len({gate["gate_digest"] for gate in reads_after_retry["gates"]}) == 1
        else:
            assert reads_after_fault == reads_before
            assert reads_after_retry == reads_before
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_competing_revision_between_read_and_append_gives_two_rows_with_one_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulates a second process revising the same candidate; one process cannot interleave."""

    runtime, method, payload, candidate_id = await prepare(tmp_path / "workspace", "validate")
    key = "step0-validate-race"
    original = SqliteControlRecordStore.append
    seen: list[str] = []

    def append(self: SqliteControlRecordStore, record: Any) -> None:
        operation = current_operation.get()
        if operation is not None and operation.idempotency_key == key:
            seen.append(record.record_type)
            if len(seen) == 2:
                competing = ControlRecordService(
                    store=self, clock=SystemClock(), ids=UuidIdGenerator()
                ).stage(
                    project_id=PROJECT,
                    namespace="MEMORY",
                    record_type="CANDIDATE",
                    state="HELD",
                    payload={**record.payload, "classification_status": "AMBIGUOUS"},
                    record_id=record.record_id,
                )
                assert competing.version == record.version
                original(self, competing)
        original(self, record)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(SqliteControlRecordStore, "append", append)
            response = await runtime.bus.dispatch(request(method, key, payload))
        found = [item for item in rows(runtime) if item["record_id"] == candidate_id]
        read = await later_reads(runtime, "validate", candidate_id, "race")
        save(
            "validate-competing-revision",
            {
                "response_error": None
                if response.error is None
                else response.error.model_dump(mode="json"),
                "candidate_rows": found,
                "candidate_read_after": read,
            },
        )
        assert response.error is None
        versions = [item["version"] for item in found]
        assert len(versions) != len(set(versions))
    finally:
        runtime.close()
