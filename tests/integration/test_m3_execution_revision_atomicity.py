"""Execution revision checkpoints (92a): source-defined reads and real post-write fault injection.

All fixtures use R0 steps (no authorization). R1-R3 start variants are outside this unit; the
authorization consumption boundary is covered by 89b and the A04 baseline. R2 sandbox execution
and complete_sandbox_attempt belong to the R2/Sandbox owner step. Attempt, effect and
reconciliation rows have no own semantic revision (EXECUTION-ATTEMPT-LEDGER-SCOPE, OPEN): this
file verifies the current contract only.
"""

from __future__ import annotations

import json
from itertools import pairwise
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import select
from tests.atomicity.harness import (
    assert_phase_delta,
    failed_command_allowances,
    snapshot,
    unchanged_or_operation_owner,
)
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_a03_storage_atomicity import setup

from thoth.adapters.storage.execution import SqliteExecutionStore
from thoth.adapters.storage.schema import metadata
from thoth.adapters.storage.sqlite import SqliteLedgerTransaction
from thoth.adapters.storage.transaction import read_connection
from thoth.domain.canonical import head_set_digest
from thoth.protocol.deferred import current_operation

OUT = Path(__file__).resolve().parents[2] / "outputs/refactor-step92-20261014"
BOOKKEEPING = {"operations", "idempotency_keys", "events", "operation_resource_bindings"}
LEDGER_POINTS = ("insert_snapshot", "insert_revision", "set_head", "insert_receipt")
WRITERS = {
    "insert_snapshot": (SqliteLedgerTransaction, "entity_snapshots", "snapshot_id"),
    "insert_revision": (SqliteLedgerTransaction, "semantic_revisions", "revision_id"),
    "insert_receipt": (SqliteLedgerTransaction, "receipts", "receipt_id"),
    "add_execution": (SqliteExecutionStore, "plan_executions", "execution_revision_id"),
    "add_attempt": (SqliteExecutionStore, "step_execution_attempts", "attempt_revision_id"),
    "add_effect": (SqliteExecutionStore, "execution_effects", "effect_id"),
    "add_reconciliation": (SqliteExecutionStore, "execution_reconciliations", "reconciliation_id"),
    "append_audit": (SqliteExecutionStore, "execution_audit", "audit_id"),
}
CASES = [
    (
        "pause",
        point,
    )
    for point in (*LEDGER_POINTS, "add_execution", "append_audit")
] + [
    ("start", "add_execution"),
    ("start", "append_audit"),
    ("start", "add_attempt"),
    ("start", "attempt_audit"),
    ("resume", "append_audit"),
    ("cancel", "add_attempt"),
    ("cancel", "append_audit"),
    ("cancel", "extra_audit"),
    ("retry", "add_attempt"),
    ("retry", "append_audit"),
    ("retry", "extra_audit"),
]
# (event type, payload key that marks the extra audit or None)
AUDITS: dict[tuple[str, str], tuple[str, str | None, bool]] = {
    ("pause", "append_audit"): ("execution/paused", None, False),
    ("start", "append_audit"): ("execution/started", None, False),
    ("start", "attempt_audit"): ("execution/attemptStarted", None, False),
    ("resume", "append_audit"): ("execution/resumed", None, False),
    ("cancel", "append_audit"): ("execution/cancelRequested", "scope", False),
    ("cancel", "extra_audit"): ("execution/cancelRequested", "scope", True),
    ("retry", "append_audit"): ("execution/attemptStarted", "retry_reason", False),
    ("retry", "extra_audit"): ("execution/attemptStarted", "retry_reason", True),
}
HANDLER = "thoth.application.commands.executions_full.ExecutionHandlers."


def save(name: str, data: dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / (name + ".json")).write_text(
        json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8"
    )


async def call(runtime: Any, method: str, key: str, payload: dict[str, Any]) -> dict[str, Any]:
    return value(await runtime.bus.dispatch(request(method, key, payload)))


def revision(digest: str) -> str:
    return "revision:" + digest


def r0_step(ctx: dict[str, Any], step_id: str, action: dict[str, Any]) -> dict[str, Any]:
    return {
        "step_id": step_id,
        "action_ref": action["action_id"],
        "inputs": ctx["spans"],
        "output_contract": {"type": "measurement"},
        "preconditions": [],
        "stop_conditions": ["result recorded"],
        "effect_vector": {"effect_completeness_confirmed": True, "external_write": False},
        "state": "READY",
    }


async def begin(path: Path) -> tuple[Any, dict[str, Any]]:
    """Project, two R0 actions and a two-step plan (a -> b); nothing is started."""

    runtime, ctx = await setup(path)
    try:
        generated = await call(
            runtime,
            "action/generate",
            "92a-prepare-actions",
            {
                "project_id": ctx["project"],
                "object_id": ctx["object"],
                "decision_need": "Observe a bounded result",
                "evidence_scope": ctx["spans"],
            },
        )
        first, second = generated["actions"][:2]
        ctx["plan"] = (
            await call(
                runtime,
                "action/plan/compose",
                "92a-prepare-plan",
                {
                    "project_id": ctx["project"],
                    "object_id": ctx["object"],
                    "plan_id": "plan:92a",
                    "selected_action_refs": [first["action_id"], second["action_id"]],
                    "step_candidates": [
                        r0_step(ctx, "step:a", first),
                        r0_step(ctx, "step:b", second),
                    ],
                    "dependency_edges": [{"from": "step:a", "to": "step:b"}],
                },
            )
        )["plan"]
        return runtime, ctx
    except BaseException:
        runtime.close()
        raise


def start_input(runtime: Any, ctx: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_id": ctx["project"],
        "plan_id": ctx["plan"]["plan_id"],
        "plan_revision_digest": ctx["plan"]["revision_digest"],
        "execution_profile_ref": "local-metadata-only",
        "expected_working_head_digest": head_set_digest(runtime.ledger.read_heads(ctx["project"])),
    }


def bound(ctx: dict[str, Any], **fields: Any) -> dict[str, Any]:
    execution = ctx["execution"]
    return {
        "project_id": ctx["project"],
        "plan_execution_id": execution["plan_execution_id"],
        "expected_execution_revision": execution["revision"],
        **fields,
    }


async def prepare(path: Path, kind: str) -> tuple[Any, dict[str, Any], str, dict[str, Any]]:
    runtime, ctx = await begin(path)
    try:
        if kind == "start":
            return runtime, ctx, "execution/start", start_input(runtime, ctx)
        started = await call(
            runtime, "execution/start", "92a-prepare-start", start_input(runtime, ctx)
        )
        ctx["execution"] = started["execution"]
        if kind == "pause":
            return runtime, ctx, "execution/pause", bound(ctx, reason="92a pause")
        if kind == "resume":
            paused = await call(
                runtime, "execution/pause", "92a-prepare-pause", bound(ctx, reason="92a prepare")
            )
            ctx["execution"] = paused["execution"]
            return (
                runtime,
                ctx,
                "execution/resume",
                bound(ctx, checkpoint_digest=ctx["execution"]["checkpoint_digest"]),
            )
        if kind == "cancel":
            return (
                runtime,
                ctx,
                "execution/cancel",
                bound(ctx, scope="step:a", reason="92a cancel"),
            )
        cancelled = await call(
            runtime,
            "execution/cancel",
            "92a-prepare-cancel",
            bound(ctx, scope="step:a", reason="92a prepare cancel"),
        )
        ctx["execution"] = cancelled["execution"]
        reconciled = await call(
            runtime,
            "execution/reconcile",
            "92a-prepare-reconcile",
            {
                "project_id": ctx["project"],
                "plan_execution_id": ctx["execution"]["plan_execution_id"],
                "attempt_id": cancelled["affected_attempts"][0]["attempt_id"],
                "target_state_evidence_refs": ctx["spans"],
                "reconciliation_method": "TARGET_CHECK_CONFIRMED_NOT_APPLIED",
            },
        )
        ctx["execution"] = reconciled["execution"]
        ctx["failed_attempt"] = reconciled["attempt"]
        return (
            runtime,
            ctx,
            "execution/retry",
            bound(
                ctx,
                step_id="step:a",
                failed_attempt_id=ctx["failed_attempt"]["attempt_id"],
                retry_reason="92a retry",
            ),
        )
    except BaseException:
        runtime.close()
        raise


def expected_reads(ctx: dict[str, Any], kind: str) -> tuple[str, ...]:
    """Source-defined top-level reads: see source-read-set-expectations.json (written first)."""

    reads = {revision(ctx["plan"]["revision_digest"])}
    if kind == "retry":
        reads |= set(ctx["spans"])
    return tuple(sorted(reads))


def install_fault(
    patch: Any, runtime: Any, ctx: dict[str, Any], command: Any, kind: str, point: str, before: Any
) -> dict[str, Any]:
    state: dict[str, Any] = {"hits": [], "violations": [], "revisions": {}, "participants": []}
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
            name
            for name in before
            if name not in BOOKKEEPING
            and not unchanged_or_operation_owner(before, external, name, command)
        )
        staged: list[dict[str, Any]] = []
        for participant in state["participants"]:
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
            staged.append({**participant, "rows": len(found)})
        state["hits"].append(
            {
                "point": point,
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
        if not all(item["rows"] for item in staged):
            state["violations"].append("an earlier participant is not staged")
        if external_changes:
            state["violations"].append("intermediate domain publication: " + str(external_changes))

    audit_event, audit_key, audit_extra = AUDITS.get((kind, point), (None, None, False))
    writer_point = "append_audit" if point in {"attempt_audit", "extra_audit"} else point

    def wrap(name: str, original: Any) -> Any:
        def call_original(instance: Any, *args: Any, **kwargs: Any) -> Any:
            selected = active()
            record = args[0]
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
                table, field = WRITERS[name][1], WRITERS[name][2]
                identity = {"project_id": project, field: getattr(record, field)}
                if name == "insert_receipt":
                    selected = selected and bool(
                        set(record.subject_refs) & state["revisions"].keys()
                    )
            result = original(instance, *args, **kwargs)
            if not selected:
                return result
            if name == "insert_revision":
                state["revisions"][record.revision_id] = record.reason
            state["participants"].append({"table": table, "identity": identity})
            target = name == writer_point
            if target and name == "append_audit":
                target = record.event_type == audit_event
                if audit_key is not None:
                    target = target and ((audit_key in record.payload) == audit_extra)
            if target:
                with read_connection(runtime.ledger.engine) as connection:
                    if name in LEDGER_POINTS and connection is not instance._connection:
                        state["violations"].append("ledger write is not on the ambient connection")
                    observe(connection, table, identity)
                raise RuntimeError("92a post-write fault: " + kind + ":" + point)
            return result

        return call_original

    for name in (*WRITERS, "set_head"):
        owner = SqliteLedgerTransaction if name == "set_head" else WRITERS[name][0]
        patch.setattr(owner, name, wrap(name, getattr(owner, name)))
    return state


async def views(runtime: Any, ctx: dict[str, Any], phase: str) -> dict[str, Any]:
    execution_id = ctx.get("execution", {}).get("plan_execution_id")
    result: dict[str, Any] = {"heads": dict(runtime.ledger.read_heads(ctx["project"]))}
    if execution_id is not None:
        base = {"project_id": ctx["project"], "plan_execution_id": execution_id}
        result["execution"] = await call(runtime, "execution/read", phase + "-read", base)
        result["attempts"] = await call(
            runtime, "execution/attempt/list", phase + "-attempts", base
        )
        result["audit"] = await call(runtime, "execution/audit/read", phase + "-audit", base)
    else:
        result["executions"] = await call(
            runtime, "execution/list", phase + "-list", {"project_id": ctx["project"]}
        )
    return result


def check_publication(runtime: Any, ctx: dict[str, Any], result: dict[str, Any]) -> None:
    record = result["execution"]
    heads = runtime.ledger.read_heads(ctx["project"])
    assert heads["EXECUTION:" + record["plan_execution_id"]] == record["revision_digest"]
    assert record["execution_revision_id"] in result["commit"]["receipt"]["subject_refs"]


@pytest.mark.parametrize("kind,point", CASES)
async def test_execution_post_write_failure_is_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, point: str
) -> None:
    runtime, ctx, method, payload = await prepare(tmp_path, kind)
    key = "92a-fault-" + kind + "-" + point
    command = request(method, key, payload)
    reads = expected_reads(ctx, kind)
    views_before = await views(runtime, ctx, "92a-before")
    before = snapshot(runtime.ledger.engine)
    try:
        registry = cast(Any, vars(runtime.bus)["_registry"])
        actual = registry.resolve(method)
        binding = actual.__module__ + "." + actual.__qualname__
        assert binding == HANDLER + kind, binding
        with monkeypatch.context() as patch:
            state = install_fault(patch, runtime, ctx, command, kind, point, before)
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
        evidence: dict[str, Any] = {
            "case": [kind, point],
            "request": command.model_dump(mode="json"),
            "error": failed.model_dump(mode="json").get("error"),
            "expected_reads": reads,
            "observed_resource_uses": actual_reads,
            "actual_callable": binding,
            "fault": state,
            "changed_tables": sorted(
                name
                for name in before
                if not unchanged_or_operation_owner(before, after, name, command)
            ),
        }
        assert len(state["hits"]) == 1, "HOLD_CONTRACT_MISMATCH: expected target hit1"
        assert not state["violations"], ("HOLD_DEFECT", state["violations"])
        assert failed.error is not None, "HOLD_DEFECT: post-write failure reported success"
        assert all(
            unchanged_or_operation_owner(before, after, t, command)
            for t in before
            if t not in BOOKKEEPING
        ), "HOLD_DEFECT: failed mutation published domain rows"
        assert actual_reads == [(ref, "READ") for ref in reads], (
            "HOLD_CONTRACT_MISMATCH: source-defined read-set differs",
            reads,
            actual_reads,
        )
        allowed = failed_command_allowances(runtime.ledger.engine, command, expected_reads=reads)
        assert_phase_delta(before, after, allowed)
        participants = {item["table"] for item in state["hits"][0]["participants"]}
        if kind in {"cancel", "retry"}:
            assert "step_execution_attempts" in participants
        if point in {"append_audit", "extra_audit", "attempt_audit"} or point in LEDGER_POINTS:
            assert "plan_executions" in participants or point in LEDGER_POINTS
        if point in {"extra_audit"}:
            assert {"plan_executions", "step_execution_attempts"} <= participants
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
        assert await views(reopened, ctx, "92a-preserved") == views_before
        recovered = value(await reopened.bus.dispatch(request(method, key + "-retry", payload)))
        check_publication(reopened, ctx, recovered)
        save(key, {**evidence, "verdict": "PASS", "replay_exact": True})
    finally:
        reopened.close()


async def change(
    runtime: Any, ctx: dict[str, Any], method: str, key: str, payload: dict[str, Any]
) -> dict[str, Any]:
    command = request(method, key, payload)
    result = value(await runtime.bus.dispatch(command))
    after = snapshot(runtime.ledger.engine)
    assert value(await runtime.bus.dispatch(command)) == result
    assert snapshot(runtime.ledger.engine) == after
    check_publication(runtime, ctx, result)
    ctx["execution"] = result["execution"]
    return result


def check_chain(runtime: Any, ctx: dict[str, Any]) -> list[Any]:
    revisions = runtime.ledger.read_revisions(
        ctx["project"], "EXECUTION", ctx["execution"]["plan_execution_id"]
    )
    for earlier, later in pairwise(revisions):
        assert later.parent_revision_digests == (earlier.revision_digest,)
    return list(revisions)


async def test_execution_revision_chain_control(tmp_path: Path) -> None:
    runtime, ctx = await begin(tmp_path)
    try:
        started = await change(
            runtime, ctx, "execution/start", "92a-control-start", start_input(runtime, ctx)
        )
        assert started["execution"]["state"] == "RUNNING" and len(started["attempts"]) == 1
        assert started["attempts"][0]["step_id"] == "step:a"
        await change(
            runtime, ctx, "execution/pause", "92a-control-pause", bound(ctx, reason="control")
        )
        assert ctx["execution"]["state"] == "PAUSED_FRONTIER"
        await change(
            runtime,
            ctx,
            "execution/resume",
            "92a-control-resume",
            bound(ctx, checkpoint_digest=ctx["execution"]["checkpoint_digest"]),
        )
        assert ctx["execution"]["state"] == "RUNNING"
        cancelled = await change(
            runtime,
            ctx,
            "execution/cancel",
            "92a-control-cancel",
            bound(ctx, scope="step:a", reason="control"),
        )
        assert [item["state"] for item in cancelled["affected_attempts"]] == ["CANCEL_REQUESTED"]
        reconciled = await change(
            runtime,
            ctx,
            "execution/reconcile",
            "92a-control-reconcile",
            {
                "project_id": ctx["project"],
                "plan_execution_id": ctx["execution"]["plan_execution_id"],
                "attempt_id": cancelled["affected_attempts"][0]["attempt_id"],
                "target_state_evidence_refs": ctx["spans"],
                "reconciliation_method": "TARGET_CHECK_CONFIRMED_NOT_APPLIED",
            },
        )
        retried = await change(
            runtime,
            ctx,
            "execution/retry",
            "92a-control-retry",
            bound(
                ctx,
                step_id="step:a",
                failed_attempt_id=reconciled["attempt"]["attempt_id"],
                retry_reason="control",
            ),
        )
        assert retried["attempt"]["attempt_number"] == 2
        revisions = check_chain(runtime, ctx)
        assert len(revisions) == 7
        expected = await views(runtime, ctx, "92a-live")
        persisted = snapshot(runtime.ledger.engine)
        save("control-execution-chain", {"revisions": len(revisions), "views": expected})
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path)
    try:
        assert snapshot(reopened.ledger.engine) == persisted
        assert await views(reopened, ctx, "92a-reopen") == expected
    finally:
        reopened.close()
