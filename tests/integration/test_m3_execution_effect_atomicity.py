"""Execution effect, reconciliation, observation, compensation and invalidation (92b).

Same evidence rules as 92a: each target writer runs the original first and then raises, with hit
count 1, the target row and every earlier participant staged on the ambient connection, no
intermediate domain publication on a separate connection, whole-DB equality afterwards, an exact
replay, a reopened runtime and a new-key retry that publishes. Read basis:
outputs/refactor-step92-20261014/source-read-set-expectations-92b.json (written before the faults).

Contract under test (D-EXECUTION, option A): reconcile NOT_APPLIED leaves the step FAILED and does
not create an attempt; the head still advances twice per reconcile. Attempt, effect and
reconciliation rows have no own semantic revision (EXECUTION-ATTEMPT-LEDGER-SCOPE, OPEN); R3
authorization consumption is 89b/A04, R2 sandbox completion is the R2 owner step.
"""

from __future__ import annotations

import json
from itertools import pairwise
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import func, select
from tests.atomicity.harness import assert_phase_delta, failed_command_allowances, snapshot
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_m3_execution_revision_atomicity import (
    BOOKKEEPING,
    HANDLER,
    begin,
    bound,
    call,
    revision,
    save,
    start_input,
    views,
)

from thoth.adapters.storage.action import SqliteActionStore
from thoth.adapters.storage.execution import SqliteExecutionStore
from thoth.adapters.storage.schema import metadata
from thoth.adapters.storage.sqlite import SqliteLedgerTransaction
from thoth.adapters.storage.transaction import read_connection
from thoth.protocol.deferred import current_operation

OWNERS = {"L": SqliteLedgerTransaction, "E": SqliteExecutionStore, "A": SqliteActionStore}
TABLES = {
    ("L", "insert_snapshot"): ("entity_snapshots", "snapshot_id"),
    ("L", "insert_revision"): ("semantic_revisions", "revision_id"),
    ("L", "insert_receipt"): ("receipts", "receipt_id"),
    ("E", "add_attempt"): ("step_execution_attempts", "attempt_revision_id"),
    ("E", "add_effect"): ("execution_effects", "effect_id"),
    ("E", "add_reconciliation"): ("execution_reconciliations", "reconciliation_id"),
    ("E", "add_execution"): ("plan_executions", "execution_revision_id"),
    ("E", "append_audit"): ("execution_audit", "audit_id"),
    ("A", "add_action"): ("action_records", "action_revision_id"),
    ("A", "append_audit"): ("action_audit", "audit_id"),
}
WRITERS = (*TABLES, ("L", "set_head"))
HANDLER_METHODS = {
    "reconcile_na": "reconcile",
    "reconcile_ap": "reconcile",
    "link": "observation_link",
    "compensation": "compensation_propose",
    "invalidate": "invalidate",
    "clear": "invalidate",
}
ROUTES = {
    "reconcile_na": "execution/reconcile",
    "reconcile_ap": "execution/reconcile",
    "link": "execution/observation/link",
    "compensation": "execution/compensation/propose",
    "invalidate": "execution/invalidate",
    "clear": "execution/invalidate",
}
Step = tuple[str, str, str | None]


def persist(event: str) -> tuple[Step, ...]:
    """One persist_execution call: ledger commit, execution row, execution audit."""

    return (
        ("L", "insert_snapshot", None),
        ("L", "insert_revision", None),
        ("L", "set_head", None),
        ("L", "insert_receipt", None),
        ("E", "add_execution", None),
        ("E", "append_audit", event),
    )


RECONCILE_ROWS: tuple[Step, ...] = (
    ("E", "add_attempt", None),
    ("E", "add_reconciliation", None),
    ("E", "add_effect", None),
)
ORDERS: dict[str, tuple[Step, ...]] = {
    # No attempt is dispatched after NOT_APPLIED; the frontier advance still writes a revision.
    "reconcile_na": (
        *RECONCILE_ROWS,
        *persist("execution/reconciliationCompleted"),
        *persist("execution/frontierChanged"),
    ),
    # Applying step:a makes step:b executable: one more attempt between the two revisions.
    "reconcile_ap": (
        *RECONCILE_ROWS,
        *persist("execution/reconciliationCompleted"),
        ("E", "add_attempt", None),
        *persist("execution/frontierChanged"),
    ),
    "link": (("E", "add_attempt", None), *persist("execution/effectChanged")),
    "compensation": (
        ("L", "insert_snapshot", None),
        ("L", "insert_revision", None),
        ("L", "set_head", None),
        ("L", "insert_receipt", None),
        ("A", "add_action", None),
        ("A", "append_audit", "action/created"),
        *persist("execution/compensationRequired"),
    ),
    "invalidate": persist("execution/invalidated"),
    "clear": (
        *persist("execution/invalidated"),
        ("E", "append_audit", "execution/pendingResultCleared"),
    ),
}
# (kind, label, owner, writer, event, occurrence of that writer/event inside the request)
CASES: list[tuple[str, str, str, str, str | None, int]] = [
    ("reconcile_na", "add_attempt", "E", "add_attempt", None, 1),
    ("reconcile_na", "add_reconciliation", "E", "add_reconciliation", None, 1),
    ("reconcile_na", "add_effect", "E", "add_effect", None, 1),
    (
        "reconcile_na",
        "audit_reconciliationCompleted",
        "E",
        "append_audit",
        "execution/reconciliationCompleted",
        1,
    ),
    ("reconcile_na", "second_set_head", "L", "set_head", None, 2),
    ("reconcile_na", "second_insert_receipt", "L", "insert_receipt", None, 2),
    (
        "reconcile_na",
        "audit_frontierChanged",
        "E",
        "append_audit",
        "execution/frontierChanged",
        1,
    ),
    ("reconcile_ap", "dispatch_add_attempt", "E", "add_attempt", None, 2),
    ("link", "add_attempt", "E", "add_attempt", None, 1),
    ("link", "audit_effectChanged", "E", "append_audit", "execution/effectChanged", 1),
    ("compensation", "action_add_action", "A", "add_action", None, 1),
    ("compensation", "action_audit_created", "A", "append_audit", "action/created", 1),
    ("compensation", "second_insert_snapshot", "L", "insert_snapshot", None, 2),
    ("compensation", "second_set_head", "L", "set_head", None, 2),
    ("compensation", "add_execution", "E", "add_execution", None, 1),
    (
        "compensation",
        "audit_compensationRequired",
        "E",
        "append_audit",
        "execution/compensationRequired",
        1,
    ),
    ("invalidate", "audit_invalidated", "E", "append_audit", "execution/invalidated", 1),
    ("clear", "audit_invalidated", "E", "append_audit", "execution/invalidated", 1),
    (
        "clear",
        "audit_pendingResultCleared",
        "E",
        "append_audit",
        "execution/pendingResultCleared",
        1,
    ),
]


def expected_prefix(kind: str, owner: str, writer: str, event: str | None, nth: int) -> list[Step]:
    order = ORDERS[kind]
    seen = 0
    for index, (own, name, label) in enumerate(order):
        if (own, name) == (owner, writer) and (event is None or label == event):
            seen += 1
            if seen == nth:
                return list(order[: index + 1])
    raise AssertionError("target is not in the source-derived writer order")


async def reconcile(
    runtime: Any, ctx: dict[str, Any], attempt_id: str, method: str, key: str
) -> dict[str, Any]:
    return await call(
        runtime,
        "execution/reconcile",
        key,
        {
            "project_id": ctx["project"],
            "plan_execution_id": ctx["execution"]["plan_execution_id"],
            "attempt_id": attempt_id,
            "target_state_evidence_refs": ctx["spans"],
            "reconciliation_method": method,
        },
    )


async def prepare(path: Path, kind: str) -> tuple[Any, dict[str, Any], str, dict[str, Any]]:
    runtime, ctx = await begin(path)
    try:
        started = await call(
            runtime, "execution/start", "92b-prepare-start", start_input(runtime, ctx)
        )
        ctx["execution"] = started["execution"]
        first = started["attempts"][0]
        route = ROUTES[kind]
        if kind == "reconcile_ap":
            payload = {
                "project_id": ctx["project"],
                "plan_execution_id": ctx["execution"]["plan_execution_id"],
                "attempt_id": first["attempt_id"],
                "target_state_evidence_refs": ctx["spans"],
                "reconciliation_method": "TARGET_CHECK_CONFIRMED_APPLIED",
            }
        elif kind == "link":
            payload = {
                "project_id": ctx["project"],
                "attempt_id": first["attempt_id"],
                "observation_refs": ctx["spans"],
                "completeness": "COMPLETE",
                "evidence_refs": ctx["spans"],
                "expected_execution_revision": ctx["execution"]["revision"],
            }
        elif kind == "invalidate":
            payload = {
                "project_id": ctx["project"],
                "plan_execution_id": ctx["execution"]["plan_execution_id"],
                "cause_revision_ref": "revision:92b-new-plan",
                "impact_refs": ["OUTCOME:stale"],
            }
        elif kind == "clear":
            payload = {
                "project_id": ctx["project"],
                "plan_execution_id": ctx["execution"]["plan_execution_id"],
                "cause_revision_ref": "revision:92b-new-plan",
                "impact_refs": ["OUTCOME:stale"],
                "expected_execution_revision": ctx["execution"]["revision"],
                "clear_pending_attempt_id": first["attempt_id"],
                "reason": "92b clear an unsettled result",
            }
        else:
            cancelled = await call(
                runtime,
                "execution/cancel",
                "92b-prepare-cancel",
                bound(ctx, scope="step:a", reason="92b prepare"),
            )
            ctx["execution"] = cancelled["execution"]
            target = cancelled["affected_attempts"][0]
            if kind == "reconcile_na":
                payload = {
                    "project_id": ctx["project"],
                    "plan_execution_id": ctx["execution"]["plan_execution_id"],
                    "attempt_id": target["attempt_id"],
                    "target_state_evidence_refs": ctx["spans"],
                    "reconciliation_method": "TARGET_CHECK_CONFIRMED_NOT_APPLIED",
                }
            else:
                partial = await reconcile(
                    runtime,
                    ctx,
                    target["attempt_id"],
                    "TARGET_CHECK_PARTIAL",
                    "92b-prepare-partial",
                )
                ctx["execution"] = partial["execution"]
                payload = {
                    "project_id": ctx["project"],
                    "attempt_id": partial["attempt"]["attempt_id"],
                    "effect_refs": ctx["spans"],
                    "reason": "92b mitigate a partial effect",
                }
        return runtime, ctx, route, payload
    except BaseException:
        runtime.close()
        raise


def expected_reads(ctx: dict[str, Any], kind: str) -> tuple[str, ...]:
    """Source-defined top-level reads: see source-read-set-expectations-92b.json."""

    reads = {revision(ctx["plan"]["revision_digest"])}
    if kind == "compensation":
        reads |= set(ctx["spans"])
    return tuple(sorted(reads))


def install_fault(
    patch: Any,
    runtime: Any,
    ctx: dict[str, Any],
    command: Any,
    target: tuple[str, str, str | None, int],
    before: Any,
) -> dict[str, Any]:
    owner_key, writer, event, nth = target
    state: dict[str, Any] = {
        "hits": [],
        "violations": [],
        "revisions": {},
        "participants": [],
        "order": [],
        "matched": 0,
    }
    project = ctx["project"]

    def active() -> bool:
        op = current_operation.get()
        return op is not None and (op.project_id, op.method, op.idempotency_key) == (
            project,
            command.method,
            command.params.meta.idempotency_key,
        )

    def observe(connection: Any, table_name: str, identity: dict[str, Any]) -> None:
        table = metadata.tables[table_name]
        rows = list(
            connection.execute(
                select(table).where(*(table.c[k] == v for k, v in identity.items()))
            ).mappings()
        )
        external = snapshot(runtime.ledger.engine)
        external_changes = sorted(
            name for name in before if name not in BOOKKEEPING and external[name] != before[name]
        )
        staged: list[dict[str, Any]] = []
        # 2026-10-15 test-side correction: SqliteLedgerTransaction.set_head (sqlite.py:122-141)
        # updates the working_heads row of an aggregate in place, so an earlier head write of the
        # same aggregate is superseded by the later one; only the latest head row is checked.
        last_head: dict[str, int] = {}
        for position, participant in enumerate(state["participants"]):
            if participant["table"] == "working_heads":
                last_head[participant["identity"]["aggregate_key"]] = position
        for position, participant in enumerate(state["participants"]):
            superseded = (
                participant["table"] == "working_heads"
                and last_head[participant["identity"]["aggregate_key"]] != position
            )
            participant_table = metadata.tables[participant["table"]]
            found = list(
                connection.execute(
                    select(participant_table).where(
                        *(
                            participant_table.c[k] == v
                            for k, v in cast("dict[str, Any]", participant["identity"]).items()
                        )
                    )
                ).mappings()
            )
            staged.append({**participant, "rows": len(found), "superseded_head": superseded})
        state["hits"].append(
            {
                "point": [owner_key, writer, event, nth],
                "table": table_name,
                "identity": identity,
                "rows": len(rows),
                "participants": staged,
                "revisions": dict(state["revisions"]),
                "same_engine": connection.engine is runtime.ledger.engine,
                "in_transaction": connection.in_transaction(),
                "external_domain_changes": external_changes,
            }
        )
        if not (rows and connection.in_transaction()):
            state["violations"].append("target row is not staged inside the transaction")
        if connection.engine is not runtime.ledger.engine:
            state["violations"].append("target write used another engine")
        if not all(item["rows"] or item["superseded_head"] for item in staged):
            state["violations"].append("an earlier participant is not staged")
        if external_changes:
            state["violations"].append("intermediate domain publication: " + str(external_changes))

    def wrap(short: str, name: str, original: Any) -> Any:
        def call_original(instance: Any, *args: Any, **kwargs: Any) -> Any:
            selected = active()
            record: Any = None if name == "set_head" else args[0]
            if name == "set_head":
                selected = selected and args[0] == project
                table = "working_heads"
                identity = {
                    "project_id": project,
                    "aggregate_key": args[1],
                    "revision_digest": args[2],
                }
            else:
                selected = selected and record.project_id == project
                table, field = TABLES[(short, name)]
                identity = {"project_id": project, field: getattr(record, field)}
                if name == "insert_receipt":
                    selected = selected and bool(
                        set(record.subject_refs) & state["revisions"].keys()
                    )
            result = original(instance, *args, **kwargs)
            if not selected:
                return result
            label = getattr(record, "event_type", None)
            if name == "insert_revision":
                state["revisions"][record.revision_id] = record.reason
            state["order"].append((short, name, label))
            state["participants"].append({"table": table, "identity": identity})
            if (short, name) == (owner_key, writer) and (event is None or label == event):
                state["matched"] += 1
                if state["matched"] == nth:
                    with read_connection(runtime.ledger.engine) as connection:
                        if short == "L" and connection is not instance._connection:
                            state["violations"].append(
                                "ledger write is not on the ambient connection"
                            )
                        observe(connection, table, identity)
                    raise RuntimeError("92b post-write fault: " + ":".join(map(str, target)))
            return result

        return call_original

    for short, name in WRITERS:
        owner = OWNERS[short]
        patch.setattr(owner, name, wrap(short, name, getattr(owner, name)))
    return state


def check_published(runtime: Any, ctx: dict[str, Any], kind: str, result: dict[str, Any]) -> None:
    record = result["execution"]
    heads = runtime.ledger.read_heads(ctx["project"])
    assert heads["EXECUTION:" + record["plan_execution_id"]] == record["revision_digest"]
    if kind == "compensation":
        action_commit, execution_commit = result["commits"]
        assert record["execution_revision_id"] in execution_commit["receipt"]["subject_refs"]
        assert result["action"]["action_revision_id"] in action_commit["receipt"]["subject_refs"]
        assert result["compensation_action_ref"] in record["compensation_refs"]
    else:
        assert record["execution_revision_id"] in result["commit"]["receipt"]["subject_refs"]


def row_count(runtime: Any, table_name: str, **where: Any) -> int:
    table = metadata.tables[table_name]
    with read_connection(runtime.ledger.engine) as connection:
        return int(
            connection.execute(
                select(func.count())
                .select_from(table)
                .where(*(table.c[k] == v for k, v in where.items()))
            ).scalar_one()
        )


def check_effect_rows(
    runtime: Any, ctx: dict[str, Any], kind: str, result: dict[str, Any]
) -> dict[str, Any]:
    """Recovered request: one reconciliation, one effect, no duplicated attempt."""

    if kind not in {"reconcile_na", "reconcile_ap"}:
        return {}
    attempt_id = result["attempt"]["attempt_id"]
    execution_id = result["execution"]["plan_execution_id"]
    counts = {
        "reconciliations": row_count(runtime, "execution_reconciliations", attempt_id=attempt_id),
        "effects": row_count(runtime, "execution_effects", attempt_id=attempt_id),
        "attempt_revisions_of_target": row_count(
            runtime, "step_execution_attempts", attempt_id=attempt_id
        ),
    }
    assert counts["reconciliations"] == 1 and counts["effects"] == 1, counts
    # The reconciled attempt has its dispatch revision and exactly one reconciled revision.
    # Dispatch revision, cancel revision (reconcile_na only) and the reconciled revision.
    assert counts["attempt_revisions_of_target"] == (3 if kind == "reconcile_na" else 2), counts
    numbered: dict[tuple[str, int], set[str]] = {}
    with read_connection(runtime.ledger.engine) as connection:
        table = metadata.tables["step_execution_attempts"]
        for row in connection.execute(
            select(table.c.attempt_id, table.c.step_id, table.c.content_json).where(
                table.c.plan_execution_id == execution_id
            )
        ).mappings():
            number = json.loads(row["content_json"])["attempt_number"]
            numbered.setdefault((row["step_id"], number), set()).add(row["attempt_id"])
    assert all(len(ids) == 1 for ids in numbered.values()), numbered
    if kind == "reconcile_na":
        assert result["new_attempts"] == []
    else:
        assert [(a["step_id"], a["attempt_number"]) for a in result["new_attempts"]] == [
            ("step:b", 1)
        ]
    return counts


@pytest.mark.parametrize(
    "kind,label,owner,writer,event,nth", CASES, ids=[f"{c[0]}-{c[1]}" for c in CASES]
)
async def test_execution_effect_post_write_failure_is_atomic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    label: str,
    owner: str,
    writer: str,
    event: str | None,
    nth: int,
) -> None:
    runtime, ctx, method, payload = await prepare(tmp_path, kind)
    key = "92b-fault-" + kind + "-" + label
    command = request(method, key, payload)
    reads = expected_reads(ctx, kind)
    views_before = await views(runtime, ctx, "92b-before")
    before = snapshot(runtime.ledger.engine)
    try:
        registry = cast(Any, vars(runtime.bus)["_registry"])
        actual = registry.resolve(method)
        binding = actual.__module__ + "." + actual.__qualname__
        assert binding == HANDLER + HANDLER_METHODS[kind], binding
        with monkeypatch.context() as patch:
            state = install_fault(patch, runtime, ctx, command, (owner, writer, event, nth), before)
            failed = await runtime.bus.dispatch(command)
        after = snapshot(runtime.ledger.engine)
        operation = next(
            json.loads(row)
            for row in after["operations"]
            if json.loads(row)["idempotency_key"] == key
        )
        binding_row = next(
            json.loads(row)
            for row in after["operation_resource_bindings"]
            if json.loads(row)["operation_id"] == operation["operation_id"]
        )
        actual_reads = sorted(
            (use["resource_ref"], use["capability"])
            for use in json.loads(binding_row["content_json"])["resource_uses"]
        )
        expected_order = expected_prefix(kind, owner, writer, event, nth)
        evidence: dict[str, Any] = {
            "case": [kind, label],
            "request": command.model_dump(mode="json"),
            "error": failed.model_dump(mode="json").get("error"),
            "expected_reads": reads,
            "observed_resource_uses": actual_reads,
            "actual_callable": binding,
            "expected_writer_order": [list(step) for step in expected_order],
            "observed_writer_order": [list(step) for step in state["order"]],
            "fault": state,
            "changed_tables": sorted(name for name in before if before[name] != after[name]),
        }
        assert len(state["hits"]) == 1, "HOLD_CONTRACT_MISMATCH: expected target hit1"
        assert state["order"] == expected_order, (
            "HOLD_CONTRACT_MISMATCH: source-derived writer order differs",
            expected_order,
            state["order"],
        )
        assert not state["violations"], ("HOLD_DEFECT", state["violations"])
        assert failed.error is not None, "HOLD_DEFECT: post-write failure reported success"
        assert all(before[t] == after[t] for t in before if t not in BOOKKEEPING), (
            "HOLD_DEFECT: failed mutation published domain rows"
        )
        assert actual_reads == [(ref, "READ") for ref in reads], (
            "HOLD_CONTRACT_MISMATCH: source-defined read-set differs",
            reads,
            actual_reads,
        )
        allowed = failed_command_allowances(runtime.ledger.engine, command, expected_reads=reads)
        assert_phase_delta(before, after, allowed)
        save(key, evidence)
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path)
    try:
        restarted = snapshot(reopened.ledger.engine)
        assert restarted == after
        replay = await reopened.bus.dispatch(command)
        assert replay.error == failed.error, "HOLD_DEFECT: replay changed the failed result"
        assert snapshot(reopened.ledger.engine) == after, (
            "HOLD_DEFECT: failed replay changed stored state"
        )
        assert await views(reopened, ctx, "92b-preserved") == views_before
        recovered = value(await reopened.bus.dispatch(request(method, key + "-retry", payload)))
        check_published(reopened, ctx, kind, recovered)
        counts = check_effect_rows(reopened, ctx, kind, recovered)
        save(key, {**evidence, "verdict": "PASS", "replay_exact": True, "recovered_rows": counts})
    finally:
        reopened.close()


async def change(
    runtime: Any, ctx: dict[str, Any], kind: str, method: str, key: str, payload: dict[str, Any]
) -> dict[str, Any]:
    command = request(method, key, payload)
    result = value(await runtime.bus.dispatch(command))
    after = snapshot(runtime.ledger.engine)
    assert value(await runtime.bus.dispatch(command)) == result
    assert snapshot(runtime.ledger.engine) == after
    check_published(runtime, ctx, kind, result)
    ctx["execution"] = result["execution"]
    return result


def check_chain(runtime: Any, ctx: dict[str, Any]) -> list[Any]:
    revisions = runtime.ledger.read_revisions(
        ctx["project"], "EXECUTION", ctx["execution"]["plan_execution_id"]
    )
    for earlier, later in pairwise(revisions):
        assert later.parent_revision_digests == (earlier.revision_digest,)
    return list(revisions)


async def test_execution_effect_chain_control(tmp_path: Path) -> None:
    runtime, ctx = await begin(tmp_path / "chain")
    try:
        await change(
            runtime, ctx, "start", "execution/start", "92b-control-start", start_input(runtime, ctx)
        )
        cancelled = await change(
            runtime,
            ctx,
            "cancel",
            "execution/cancel",
            "92b-control-cancel",
            bound(ctx, scope="step:a", reason="92b control"),
        )
        failed_attempt = cancelled["affected_attempts"][0]
        not_applied = await change(
            runtime,
            ctx,
            "reconcile_na",
            "execution/reconcile",
            "92b-control-reconcile-na",
            {
                "project_id": ctx["project"],
                "plan_execution_id": ctx["execution"]["plan_execution_id"],
                "attempt_id": failed_attempt["attempt_id"],
                "target_state_evidence_refs": ctx["spans"],
                "reconciliation_method": "TARGET_CHECK_CONFIRMED_NOT_APPLIED",
            },
        )
        assert not_applied["new_attempts"] == [] and not_applied["retry_permission"] is True
        retried = await change(
            runtime,
            ctx,
            "retry",
            "execution/retry",
            "92b-control-retry",
            bound(
                ctx,
                step_id="step:a",
                failed_attempt_id=not_applied["attempt"]["attempt_id"],
                retry_reason="92b control",
            ),
        )
        assert retried["attempt"]["attempt_number"] == 2
        partial = await change(
            runtime,
            ctx,
            "reconcile_na",
            "execution/reconcile",
            "92b-control-reconcile-partial",
            {
                "project_id": ctx["project"],
                "plan_execution_id": ctx["execution"]["plan_execution_id"],
                "attempt_id": retried["attempt"]["attempt_id"],
                "target_state_evidence_refs": ctx["spans"],
                "reconciliation_method": "TARGET_CHECK_PARTIAL",
            },
        )
        assert partial["attempt"]["state"] == "PARTIAL"
        linked = await change(
            runtime,
            ctx,
            "link",
            "execution/observation/link",
            "92b-control-link",
            {
                "project_id": ctx["project"],
                "attempt_id": partial["attempt"]["attempt_id"],
                "observation_refs": ctx["spans"],
                "completeness": "COMPLETE",
                "evidence_refs": ctx["spans"],
                "expected_execution_revision": ctx["execution"]["revision"],
            },
        )
        assert linked["downstream_outcome_readiness"] == "READY"
        compensated = await change(
            runtime,
            ctx,
            "compensation",
            "execution/compensation/propose",
            "92b-control-compensation",
            {
                "project_id": ctx["project"],
                "attempt_id": partial["attempt"]["attempt_id"],
                "effect_refs": ctx["spans"],
                "reason": "92b control mitigation",
            },
        )
        assert compensated["execution"]["state"] == "COMPENSATION_PENDING"
        invalidated = await change(
            runtime,
            ctx,
            "invalidate",
            "execution/invalidate",
            "92b-control-invalidate",
            {
                "project_id": ctx["project"],
                "plan_execution_id": ctx["execution"]["plan_execution_id"],
                "cause_revision_ref": "revision:92b-new-plan",
                "impact_refs": ["OUTCOME:stale"],
            },
        )
        assert invalidated["execution"]["state"] == "INVALIDATED"
        revisions = check_chain(runtime, ctx)
        action_head = [
            key
            for key in runtime.ledger.read_heads(ctx["project"])
            if key.endswith(compensated["compensation_action_ref"])
        ]
        assert action_head, "compensation Action head is missing"
        expected = await views(runtime, ctx, "92b-live")
        persisted = snapshot(runtime.ledger.engine)
        save(
            "control-execution-effect-chain",
            {"revisions": len(revisions), "action_head": action_head, "views": expected},
        )
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path / "chain")
    try:
        assert snapshot(reopened.ledger.engine) == persisted
        assert await views(reopened, ctx, "92b-reopen") == expected
    finally:
        reopened.close()


async def test_execution_clear_pending_result_control(tmp_path: Path) -> None:
    runtime, ctx, method, payload = await prepare(tmp_path / "clear", "clear")
    try:
        cleared = await change(runtime, ctx, "clear", method, "92b-control-clear", payload)
        assert cleared["execution"]["state"] == "INVALIDATED"
        audit = await call(
            runtime,
            "execution/audit/read",
            "92b-control-clear-audit",
            {
                "project_id": ctx["project"],
                "plan_execution_id": cleared["execution"]["plan_execution_id"],
            },
        )
        # audit_read returns its rows under "records" (executions_full.py:189-197).
        events = [item["event_type"] for item in audit["records"]]
        assert events[-2:] == ["execution/invalidated", "execution/pendingResultCleared"]
        check_chain(runtime, ctx)
        persisted = snapshot(runtime.ledger.engine)
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path / "clear")
    try:
        assert snapshot(reopened.ledger.engine) == persisted
    finally:
        reopened.close()
