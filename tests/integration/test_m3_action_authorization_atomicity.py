"""Action authorization checkpoints (89b): row + audit all-or-nothing under the current contract.

Authorization rows carry no semantic revision, head or receipt (ACTION-AUTH-LEDGER-SCOPE, OPEN):
this file verifies the contract as it is and neither declares that a defect nor creates one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import func, select
from tests.atomicity.harness import assert_phase_delta, failed_command_allowances, snapshot
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import prepare_project, request, value
from tests.integration.test_a04_storage_authority import (
    approve_required_roles,
    prepare_r3,
    start_request,
)

from thoth.adapters.storage.action import SqliteActionStore
from thoth.adapters.storage.schema import metadata, plan_executions, step_execution_attempts
from thoth.adapters.storage.sqlite import SqliteLedgerTransaction
from thoth.adapters.storage.transaction import read_connection
from thoth.protocol.deferred import current_operation

OUT = Path(__file__).resolve().parents[2] / "outputs/refactor-step89-20261014"
BOOKKEEPING = {"operations", "idempotency_keys", "events", "operation_resource_bindings"}
LEDGER_POINTS = ("insert_snapshot", "insert_revision", "set_head", "insert_receipt")
WRITERS = {
    "insert_snapshot": (SqliteLedgerTransaction, "entity_snapshots", "snapshot_id"),
    "insert_revision": (SqliteLedgerTransaction, "semantic_revisions", "revision_id"),
    "insert_receipt": (SqliteLedgerTransaction, "receipts", "receipt_id"),
    "add_plan": (SqliteActionStore, "action_plans", "plan_revision_id"),
    "add_authorization": (SqliteActionStore, "action_authorizations", "authorization_revision_id"),
    "append_audit": (SqliteActionStore, "action_audit", "audit_id"),
}
CASES = [
    ("prepare", "add_authorization"),
    ("prepare", "append_audit"),
    ("decide", "add_authorization"),
    ("decide", "append_audit"),
    ("stale", "add_authorization"),
    ("stale", "append_audit"),
    ("consume", "append_audit"),
]
EVENTS = {
    "prepare": "action/authorizationPrepared",
    "decide": "action/authorizationDecided",
    "stale": "action/authorizationStale",
    "consume": "action/authorizationConsumed",
}
STATES = {
    "prepare": {"PENDING"},
    "decide": {"PENDING", "APPROVED"},
    "stale": {"STALE"},
}
HANDLERS = {
    "prepare": "thoth.application.commands.actions_full.ActionHandlers.authorization_prepare",
    "decide": "thoth.application.commands.actions_full.ActionHandlers.authorization_decide",
    "stale": "thoth.application.commands.actions_full.ActionHandlers.step_revise",
    "consume": "thoth.application.commands.executions_full.ExecutionHandlers.start",
}
CONTENT: dict[str, Any] = {
    "body_text": "89b protected body",
    "attachments": [{"name": "request.pdf", "digest": "1" * 64}],
}


def save(name: str, data: dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / (name + ".json")).write_text(
        json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8"
    )


async def call(runtime: Any, method: str, key: str, payload: dict[str, Any]) -> dict[str, Any]:
    return value(await runtime.bus.dispatch(request(method, key, payload)))


def revision(digest: str) -> str:
    return "revision:" + digest


async def r3_plan(path: Path) -> tuple[Any, dict[str, Any]]:
    """The public-RPC front half of prepare_r3, stopped before the authorization is prepared."""

    runtime, project = await prepare_project(path)
    try:
        await call(
            runtime,
            "project/role/assign",
            "89b-owner",
            {
                "project_id": project,
                "expected_revision": 0,
                "actor_id": "human:owner",
                "role": "project-owner",
                "scope": "PROJECT",
                "authority_tags": ["R3_ACTION_OWNER"],
            },
        )
        thread = await call(
            runtime,
            "thread/start",
            "89b-thread",
            {
                "project_id": project,
                "thread_id": "thread:r3",
                "problem": "Prepare a protected local record",
            },
        )
        object_id = thread["current_object_ids"][0]
        action = (
            await call(
                runtime,
                "action/create",
                "89b-action",
                {
                    "project_id": project,
                    "object_id": object_id,
                    "primary_purpose": "STATE_OR_DESIGN_CHANGE",
                    "specification": {
                        "description": "Synthetic protected configuration change",
                        "expected_observation_or_change": {
                            "description": "configuration target changes"
                        },
                        "effect_completeness_confirmed": True,
                        "stop_conditions": ["unexpected effect"],
                        "observability": "configuration receipt",
                        "effect_vector": {
                            "effect_completeness_confirmed": True,
                            "external_write": True,
                        },
                    },
                    "evidence_refs": [],
                },
            )
        )["action"]
        plan = (
            await call(
                runtime,
                "action/plan/compose",
                "89b-plan",
                {
                    "project_id": project,
                    "object_id": object_id,
                    "plan_id": "plan:r3",
                    "selected_action_refs": [action["action_id"]],
                    "step_candidates": [
                        {
                            "step_id": "step:r3",
                            "action_ref": action["action_id"],
                            "inputs": [],
                            "target_digests": ["b" * 64],
                            "output_contract": {"type": "configuration-receipt"},
                            "preconditions": [],
                            "stop_conditions": ["unexpected effect"],
                            "state": "READY",
                            "effect_vector": {
                                "effect_completeness_confirmed": True,
                                "external_write": True,
                            },
                        }
                    ],
                    "dependency_edges": [],
                },
            )
        )["plan"]
        return runtime, {"project": project, "plan": plan, "action": action}
    except BaseException:
        runtime.close()
        raise


def prepare_input(ctx: dict[str, Any]) -> dict[str, Any]:
    plan = ctx["plan"]
    return {
        "project_id": ctx["project"],
        "plan_id": plan["plan_id"],
        "step_id": "step:r3",
        "plan_revision_digest": plan["revision_digest"],
        "predecessor_output_digests": [],
        "target_baseline_digests": ["b" * 64],
        "policy_version": "policy:current",
    }


async def first_required_role(
    runtime: Any, project: str, authorization: dict[str, Any]
) -> dict[str, Any]:
    roles = (await call(runtime, "project/role/list", "89b-roles", {"project_id": project}))[
        "roles"
    ]
    name = authorization["required_roles"][0]
    found = next(
        (
            item
            for item in roles
            if item["role"] == name
            and item["actor_id"] == "human:owner"
            and item["state"] == "ACTIVE"
        ),
        None,
    )
    if found is not None:
        return cast("dict[str, Any]", found)
    current = await call(runtime, "project/read", "89b-project", {"project_id": project})
    return cast(
        "dict[str, Any]",
        (
            await call(
                runtime,
                "project/role/assign",
                "89b-assign",
                {
                    "project_id": project,
                    "expected_revision": current["revision"],
                    "actor_id": "human:owner",
                    "role": name,
                    "scope": "PROJECT",
                    "authority_tags": ["R3_ACTION_OWNER"],
                },
            )
        )["role"],
    )


async def prepare(path: Path, kind: str) -> tuple[Any, dict[str, Any], Any]:
    """Return the runtime, the context and the request that the fault will hit."""

    if kind == "consume":
        runtime, project, plan, approved = await prepare_r3(path)
        ctx = {"project": project, "plan": plan, "authorization": approved}
        return runtime, ctx, start_request(runtime, project, plan, "89b-fault-consume")
    runtime, ctx = await r3_plan(path)
    try:
        if kind == "prepare":
            return runtime, ctx, ("action/authorization/prepare", prepare_input(ctx))
        ctx["authorization"] = (
            await call(runtime, "action/authorization/prepare", "89b-prepare", prepare_input(ctx))
        )["authorization"]
        if kind == "stale":
            return (
                runtime,
                ctx,
                (
                    "action/step/revise",
                    {
                        "project_id": ctx["project"],
                        "plan_id": ctx["plan"]["plan_id"],
                        "step_id": "step:r3",
                        "patch": {"content": CONTENT},
                        "evidence_refs": [],
                        "reason": "89b protected content change",
                        "expected_plan_revision": ctx["plan"]["revision_digest"],
                    },
                ),
            )
        role = await first_required_role(runtime, ctx["project"], ctx["authorization"])
        return (
            runtime,
            ctx,
            (
                "action/authorization/decide",
                {
                    "project_id": ctx["project"],
                    "authorization_id": ctx["authorization"]["authorization_id"],
                    "decision": "APPROVE",
                    "actor_ref": "human:owner",
                    "role_assignment_ref": role["role_assignment_id"],
                    "approved_digest": ctx["authorization"]["exact_scope_digest"],
                },
            ),
        )
    except BaseException:
        runtime.close()
        raise


def expected_reads(ctx: dict[str, Any], kind: str) -> tuple[str, ...]:
    """Source-defined top-level reads: see source-read-set-expectations-89b.json (written first)."""

    if kind == "decide":
        return (revision(ctx["authorization"]["plan_revision_digest"]),)
    return (revision(ctx["plan"]["revision_digest"]),)


def install_fault(
    patch: Any, runtime: Any, ctx: dict[str, Any], command: Any, kind: str, point: str, before: Any
) -> dict[str, Any]:
    state: dict[str, Any] = {"hits": [], "violations": [], "revisions": {}, "participants": []}
    project = ctx["project"]
    wanted_event = EVENTS[kind]

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
            target = False
            if point == name == "add_authorization":
                target = record.state in STATES.get(kind, set()) or kind == "consume"
            elif point == name == "append_audit":
                target = record.event_type == wanted_event
            if target:
                with read_connection(runtime.ledger.engine) as connection:
                    observe(connection, table, identity)
                raise RuntimeError("89b post-write fault: " + kind + ":" + point)
            return result

        return call_original

    for name in (*LEDGER_POINTS, "add_plan", "add_authorization", "append_audit"):
        owner = SqliteLedgerTransaction if name in LEDGER_POINTS else WRITERS[name][0]
        patch.setattr(owner, name, wrap(name, getattr(owner, name)))
    return state


def row_count(runtime: Any, table: Any) -> int:
    with runtime.ledger.engine.connect() as connection:
        return int(connection.execute(select(func.count()).select_from(table)).scalar_one())


async def views(runtime: Any, ctx: dict[str, Any], phase: str) -> dict[str, Any]:
    base = {"project_id": ctx["project"]}
    result: dict[str, Any] = {
        "audit": await call(runtime, "action/audit/read", phase + "-audit", base),
        "plan": (
            await call(
                runtime,
                "action/plan/read",
                phase + "-plan",
                {**base, "plan_id": ctx["plan"]["plan_id"]},
            )
        )["plan"],
        "heads": dict(runtime.ledger.read_heads(ctx["project"])),
    }
    if ctx.get("authorization"):
        result["authorization"] = (
            await call(
                runtime,
                "action/authorization/read",
                phase + "-authorization",
                {**base, "authorization_id": ctx["authorization"]["authorization_id"]},
            )
        )["authorization"]
    return result


@pytest.mark.parametrize("kind,point", CASES)
async def test_authorization_post_write_failure_is_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, point: str
) -> None:
    runtime, ctx, prepared = await prepare(tmp_path, kind)
    key = "89b-fault-" + kind + "-" + point
    if kind == "consume":
        command = start_request(runtime, ctx["project"], ctx["plan"], key)
        method = command.method
        payload: dict[str, Any] = dict(command.params.input)
    else:
        method, payload = prepared
        command = request(method, key, payload)
    reads = expected_reads(ctx, kind)
    views_before = await views(runtime, ctx, "89b-before")
    before = snapshot(runtime.ledger.engine)
    counts_before = (
        row_count(runtime, plan_executions),
        row_count(runtime, step_execution_attempts),
    )
    try:
        registry = cast(Any, vars(runtime.bus)["_registry"])
        actual = registry.resolve(method)
        binding = actual.__module__ + "." + actual.__qualname__
        assert binding == HANDLERS[kind], binding
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
            "changed_tables": sorted(name for name in before if before[name] != after[name]),
            "execution_rows_before_after": [
                counts_before,
                (row_count(runtime, plan_executions), row_count(runtime, step_execution_attempts)),
            ],
        }
        assert len(state["hits"]) == 1, "HOLD_CONTRACT_MISMATCH: expected target hit1"
        assert not state["violations"], ("HOLD_DEFECT", state["violations"])
        assert failed.error is not None, "HOLD_DEFECT: post-write failure reported success"
        assert all(before[t] == after[t] for t in before if t not in BOOKKEEPING), (
            "HOLD_DEFECT: failed mutation published domain rows"
        )
        assert evidence["execution_rows_before_after"][1] == counts_before
        assert actual_reads == [(ref, "READ") for ref in reads], (
            "HOLD_CONTRACT_MISMATCH: source-defined read-set differs",
            reads,
            actual_reads,
        )
        allowed = failed_command_allowances(runtime.ledger.engine, command, expected_reads=reads)
        assert_phase_delta(before, after, allowed)
        participants = {item["table"] for item in state["hits"][0]["participants"]}
        assert "action_authorizations" in participants
        if kind == "stale":
            assert {"action_plans", "semantic_revisions"} <= participants
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
        assert await views(reopened, ctx, "89b-preserved") == views_before
        recovered = value(await reopened.bus.dispatch(request(method, key + "-retry", payload)))
        after_retry = await views(reopened, ctx, "89b-recovered")
        if kind == "prepare":
            assert recovered["authorization"]["state"] == "PENDING"
        elif kind == "decide":
            assert recovered["authorization"]["decision_history"]
            assert after_retry["authorization"] == recovered["authorization"]
        elif kind == "stale":
            assert recovered["plan"]["revision_digest"] != ctx["plan"]["revision_digest"]
            assert after_retry["authorization"]["state"] == "STALE"
            assert after_retry["authorization"]["stale_reason"] == "MATERIAL_CHANGE"
        else:
            assert views_before["authorization"]["state"] == "APPROVED"
            assert views_before["authorization"].get("consumed_at") is None
            assert after_retry["authorization"]["state"] == "CONSUMED"
            assert row_count(reopened, step_execution_attempts) == 1
        save(key, {**evidence, "verdict": "PASS", "replay_exact": True})
    finally:
        reopened.close()


async def change(runtime: Any, method: str, key: str, payload: dict[str, Any]) -> dict[str, Any]:
    command = request(method, key, payload)
    result = value(await runtime.bus.dispatch(command))
    after = snapshot(runtime.ledger.engine)
    assert value(await runtime.bus.dispatch(command)) == result
    assert snapshot(runtime.ledger.engine) == after
    return result


async def read_authorization(
    runtime: Any, ctx: dict[str, Any], key: str, item: dict[str, Any]
) -> Any:
    return (
        await call(
            runtime,
            "action/authorization/read",
            key,
            {"project_id": ctx["project"], "authorization_id": item["authorization_id"]},
        )
    )["authorization"]


async def test_authorization_lifecycle_control(tmp_path: Path) -> None:
    runtime, ctx = await r3_plan(tmp_path)
    project = ctx["project"]
    try:
        prepared = (
            await change(
                runtime, "action/authorization/prepare", "89b-control-prepare", prepare_input(ctx)
            )
        )["authorization"]
        assert prepared["state"] == "PENDING" and prepared["single_use"] is True
        ctx["authorization"] = prepared
        role = await first_required_role(runtime, project, prepared)
        decided = (
            await change(
                runtime,
                "action/authorization/decide",
                "89b-control-decide",
                {
                    "project_id": project,
                    "authorization_id": prepared["authorization_id"],
                    "decision": "APPROVE",
                    "actor_ref": "human:owner",
                    "role_assignment_ref": role["role_assignment_id"],
                    "approved_digest": prepared["exact_scope_digest"],
                },
            )
        )["authorization"]
        assert decided["supersedes_revision_digest"] == prepared["revision_digest"]
        approved = await approve_required_roles(
            runtime, project, decided, "human:owner", "89b-control-approve"
        )
        assert approved["state"] == "APPROVED" and approved.get("consumed_at") is None
        assert await read_authorization(runtime, ctx, "89b-control-read", approved) == approved
        revised = (
            await change(
                runtime,
                "action/step/revise",
                "89b-control-revise",
                {
                    "project_id": project,
                    "plan_id": ctx["plan"]["plan_id"],
                    "step_id": "step:r3",
                    "patch": {"content": CONTENT},
                    "evidence_refs": [],
                    "reason": "89b protected content change",
                    "expected_plan_revision": ctx["plan"]["revision_digest"],
                },
            )
        )["plan"]
        stale = await read_authorization(runtime, ctx, "89b-control-stale", approved)
        assert stale["state"] == "STALE" and stale["stale_reason"] == "MATERIAL_CHANGE"
        assert stale["decision_history"][:-1] == approved["decision_history"]
        ctx["plan"] = revised
        again = (
            await change(
                runtime,
                "action/authorization/prepare",
                "89b-control-reprepare",
                prepare_input(ctx),
            )
        )["authorization"]
        ctx["authorization"] = await approve_required_roles(
            runtime, project, again, "human:owner", "89b-control-reapprove"
        )
        started = value(
            await runtime.bus.dispatch(start_request(runtime, project, ctx["plan"], "89b-start"))
        )
        assert started
        consumed = await read_authorization(runtime, ctx, "89b-control-consumed", again)
        assert consumed["state"] == "CONSUMED" and consumed["consumed_at"] is not None
        expected = await views(runtime, ctx, "89b-live")
        persisted = snapshot(runtime.ledger.engine)
        save("control-authorization-lifecycle", {"views": expected})
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path)
    try:
        assert snapshot(reopened.ledger.engine) == persisted
        assert await views(reopened, ctx, "89b-reopen") == expected
    finally:
        reopened.close()
