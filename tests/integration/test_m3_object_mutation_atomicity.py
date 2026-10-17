"""Object mutation checkpoints: source-defined reads and real post-write fault injection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import event, select
from tests.atomicity.harness import assert_phase_delta, failed_command_allowances, snapshot
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_a03_storage_atomicity import setup

from thoth.adapters.storage.decision_object import SqliteDecisionObjectStore
from thoth.adapters.storage.dependency import SqliteDependencyGraph
from thoth.adapters.storage.schema import metadata
from thoth.adapters.storage.sqlite import SqliteLedgerTransaction
from thoth.adapters.storage.transaction import read_connection
from thoth.protocol.deferred import current_operation

OUT = Path(__file__).resolve().parents[2] / "outputs/refactor-step84-20261014"
BOOKKEEPING = {"operations", "idempotency_keys", "events", "operation_resource_bindings"}
FRAME_POINTS = (
    "insert_snapshot",
    "insert_revision",
    "set_head",
    "insert_receipt",
    "add_object",
    "append_audit",
)
LATE_KINDS = (
    "profile",
    "facet",
    "work",
    "relation_add",
    "relation_remove",
    "revalidate",
    "ack",
    "clear",
)
CASES = (
    [("frame", p) for p in FRAME_POINTS]
    + [(k, "append_audit") for k in LATE_KINDS]
    + [(k, "proposal_insert") for k in ("split", "merge")]
)


def save(name: str, data: dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / (name + ".json")).write_text(
        json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8"
    )


async def read_object(
    runtime: Any, ctx: dict[str, Any], key: str, digest: str | None = None
) -> dict[str, Any]:
    payload = {"project_id": ctx["project"], "object_id": ctx["object"]}
    if digest is not None:
        payload["revision_digest"] = digest
    return value(await runtime.bus.dispatch(request("object/read", key, payload)))["object"]


async def begin(path: Path) -> tuple[Any, dict[str, Any]]:
    runtime, ctx = await setup(path)
    ctx["current"] = await read_object(runtime, ctx, "84-setup-read")
    return runtime, ctx


def command_input(ctx: dict[str, Any], kind: str) -> tuple[str, dict[str, Any], str]:
    current = ctx["current"]
    base = {"project_id": ctx["project"], "object_id": ctx["object"]}
    revision = {**base, "expected_revision_digest": current["revision_digest"]}
    evidence = {"evidence_refs": ctx["spans"]}
    if kind == "frame":
        return (
            "object/frame/revise",
            {
                **revision,
                **evidence,
                "frame_patch": {"purpose_statement": "84 revised purpose"},
                "reason": "84 frame",
            },
            "object/updated",
        )
    if kind == "profile":
        return (
            "object/profile/apply",
            {
                **revision,
                **evidence,
                "profile_refs": ["SYSTEMS_INTEGRATION"],
                "reason": "84 profile",
            },
            "object/profileChanged",
        )
    if kind == "facet":
        return (
            "object/facet/update",
            {
                **revision,
                **evidence,
                "add": ["TECHNICAL", "ARBITRARY_EXTENSION"],
                "remove": [],
                "reason": "84 facet",
            },
            "object/updated",
        )
    if kind == "work":
        return (
            "object/work/replan",
            {**base, "revision_digest": current["revision_digest"], "trigger_refs": ctx["spans"]},
            "object/workModeChanged",
        )
    if kind == "relation_add":
        return (
            "object/relation/add",
            {
                **revision,
                **evidence,
                "relation_type": "DEPENDS_ON",
                "target_ref": "CRITERION:latency",
                "semantic_role": "84 dependency",
                "authority_state": "OFFICIAL",
            },
            "object/relationChanged",
        )
    if kind == "relation_remove":
        return (
            "object/relation/remove",
            {
                **revision,
                **evidence,
                "relation_id": ctx["relation"]["relation_id"],
                "reason": "84 ending relation",
            },
            "object/relationChanged",
        )
    if kind == "revalidate":
        return (
            "object/revalidate",
            {
                **base,
                "revision_digest": current["revision_digest"],
                "trigger_reason": "84 profile condition",
            },
            "object/attentionRaised",
        )
    if kind in {"ack", "clear"}:
        return (
            "object/attention/acknowledge",
            {
                **revision,
                "attention_id": ctx["attention"]["attention_id"],
                "actor_ref": "human:integrator",
                "note": "84 attention",
            },
            "object/attentionCleared" if kind == "clear" else "object/updated",
        )
    if kind == "split":
        return (
            "object/split/propose",
            {
                **revision,
                **evidence,
                "partitions": [{"purpose_statement": "84 left"}, {"purpose_statement": "84 right"}],
                "rationale": "84 preview only",
            },
            "object/splitProposed",
        )
    second = ctx["second"]
    return (
        "object/merge/propose",
        {
            "project_id": ctx["project"],
            **evidence,
            "object_ids": [ctx["object"], second["object_id"]],
            "expected_revision_digests": [current["revision_digest"], second["revision_digest"]],
            "field_mapping": {"purpose_statement": "retain both"},
            "rationale": "84 preview only",
        },
        "object/mergeProposed",
    )


async def change(
    runtime: Any, ctx: dict[str, Any], kind: str, key: str, patch: dict[str, Any] | None = None
) -> dict[str, Any]:
    method, payload, _ = command_input(ctx, kind)
    if patch:
        payload.update(patch)
    command = request(method, key, payload)
    result = value(await runtime.bus.dispatch(command))
    after = snapshot(runtime.ledger.engine)
    assert value(await runtime.bus.dispatch(command)) == result
    assert snapshot(runtime.ledger.engine) == after
    if "object" in result:
        ctx["current"] = result["object"]
        record = ctx["current"]
        assert (
            runtime.ledger.read_heads(ctx["project"])["DECISION_OBJECT:" + ctx["object"]]
            == record["revision_digest"]
        )
        assert record["object_revision_id"] in result["commit"]["receipt"]["subject_refs"]
        assert await read_object(runtime, ctx, key + "-read") == record
    if "relation" in result:
        ctx["relation"] = result["relation"]
    if "attention" in result:
        ctx["attention"] = result["attention"]
    return result


async def attention_items(runtime: Any, ctx: dict[str, Any], key: str) -> list[dict[str, Any]]:
    items = value(
        await runtime.bus.dispatch(
            request("object/attention/list", key, {"project_id": ctx["project"]})
        )
    )["attention_items"]
    return [item for item in items if item["object_id"] == ctx["object"]]


async def prepare_attention(runtime: Any, ctx: dict[str, Any], phase: str) -> None:
    await change(runtime, ctx, "profile", phase + "-profile")
    await change(
        runtime, ctx, "frame", phase + "-missing", {"frame_patch": {"workstream_refs": []}}
    )


async def prepare(path: Path, kind: str) -> tuple[Any, dict[str, Any]]:
    runtime, ctx = await begin(path)
    try:
        if kind == "relation_remove":
            await change(runtime, ctx, "relation_add", "84-prepare-relation")
        if kind in {"revalidate", "ack", "clear"}:
            await prepare_attention(runtime, ctx, "84-prepare")
        if kind in {"ack", "clear"}:
            await change(runtime, ctx, "revalidate", "84-prepare-blocker")
            ctx["attention"] = next(
                item
                for item in await attention_items(runtime, ctx, "84-prepare-attention")
                if item["condition_ref"] == "MISSING:workstream_refs" and item["state"] == "OPEN"
            )
        if kind == "clear":
            await change(runtime, ctx, "ack", "84-prepare-ack")
            await change(
                runtime,
                ctx,
                "frame",
                "84-prepare-restored",
                {"frame_patch": {"workstream_refs": ["integration"]}},
            )
            await change(runtime, ctx, "revalidate", "84-prepare-valid")
        if kind == "merge":
            ctx["second"] = value(
                await runtime.bus.dispatch(
                    request(
                        "object/materialize",
                        "84-second",
                        {
                            "project_id": ctx["project"],
                            "thread_id": "thread:a03-storage",
                            "purpose_statement": "84 second Object",
                            "focus_refs": ctx["spans"],
                            "trigger_evidence_refs": ctx["spans"],
                            "profile_refs": ["GENERAL_RND_DECISION"],
                        },
                    )
                )
            )["object"]
        return runtime, ctx
    except BaseException:
        runtime.close()
        raise


async def views(runtime: Any, ctx: dict[str, Any], phase: str) -> dict[str, Any]:
    base = {"project_id": ctx["project"], "object_id": ctx["object"]}
    result = {
        "object": await read_object(runtime, ctx, phase + "-object"),
        "attention": await attention_items(runtime, ctx, phase + "-attention"),
    }
    for method in ("object/relation/list", "object/impact/read", "object/audit/read"):
        result[method] = value(await runtime.bus.dispatch(request(method, phase + method, base)))
    result["heads"] = runtime.ledger.read_heads(ctx["project"])
    return result


async def check_reopened(
    path: Path, ctx: dict[str, Any], before: Any, expected: dict[str, Any]
) -> None:
    reopened = create_runtime(path)
    try:
        assert snapshot(reopened.ledger.engine) == before
        assert await views(reopened, ctx, "84-reopen") == expected
    finally:
        reopened.close()


async def test_revision_relation_work_control(tmp_path: Path) -> None:
    runtime, ctx = await begin(tmp_path)
    original = ctx["current"]
    results: dict[str, Any] = {}
    try:
        for kind in ("frame", "profile", "facet", "relation_add", "relation_remove", "work"):
            results[kind] = await change(runtime, ctx, kind, "84-control-" + kind)
        assert results["facet"]["accepted_facets"] == ["TECHNICAL"]
        assert results["facet"]["rejected_extensions"] == ["ARBITRARY_EXTENSION"]
        assert results["relation_remove"]["relation"]["active"] is False
        assert (
            await read_object(runtime, ctx, "84-original", original["revision_digest"]) == original
        )
        method, payload, _ = command_input(ctx, "frame")
        before_conflict = snapshot(runtime.ledger.engine)
        assert (
            await runtime.bus.dispatch(request(method, "84-control-frame", payload))
        ).error is not None
        assert snapshot(runtime.ledger.engine) == before_conflict
        expected = await views(runtime, ctx, "84-live")
        persisted = snapshot(runtime.ledger.engine)
        save(
            "control-revision-relation",
            {"results": results, "views": expected, "original_preserved": True},
        )
    finally:
        runtime.close()
    await check_reopened(tmp_path, ctx, persisted, expected)


async def test_attention_lifecycle_control(tmp_path: Path) -> None:
    runtime, ctx = await begin(tmp_path)
    try:
        await prepare_attention(runtime, ctx, "84-control")
        await change(runtime, ctx, "revalidate", "84-control-blocked")
        assert "MISSING:workstream_refs" in ctx["current"]["blockers"]
        items = await attention_items(runtime, ctx, "84-control-items")
        ctx["attention"] = next(
            item
            for item in items
            if item["condition_ref"] == "MISSING:workstream_refs" and item["state"] == "OPEN"
        )
        identifier = ctx["attention"]["attention_id"]
        await change(runtime, ctx, "revalidate", "84-control-repeat-condition")
        repeated = await attention_items(runtime, ctx, "84-control-repeated-items")
        assert [item["attention_id"] for item in repeated] == [identifier]
        ack = await change(runtime, ctx, "ack", "84-control-ack")
        assert ack["attention"]["state"] == "ACKNOWLEDGED"
        await change(
            runtime,
            ctx,
            "frame",
            "84-control-fix-field",
            {"frame_patch": {"workstream_refs": ["integration"]}},
        )
        await change(runtime, ctx, "revalidate", "84-control-valid")
        assert ctx["current"]["blockers"] == []
        cleared = await change(runtime, ctx, "clear", "84-control-clear")
        assert (
            cleared["attention"]["state"] == "CLEARED" and cleared["object"]["attention"] == "NONE"
        )
        expected = await views(runtime, ctx, "84-live")
        persisted = snapshot(runtime.ledger.engine)
        save("control-attention", {"ack": ack, "cleared": cleared, "views": expected})
    finally:
        runtime.close()
    await check_reopened(tmp_path, ctx, persisted, expected)


def expected_reads(ctx: dict[str, Any], kind: str) -> tuple[str, ...]:
    reads = {"revision:" + ctx["current"]["revision_digest"]}
    if kind == "relation_remove":
        reads.add("control:" + ctx["relation"]["revision_digest"])
    if kind in {"ack", "clear"}:
        reads.add("control:" + ctx["attention"]["revision_digest"])
    if kind == "merge":
        reads.add("revision:" + ctx["second"]["revision_digest"])
    return tuple(sorted(reads))


def install_fault(
    patch: Any, runtime: Any, ctx: dict[str, Any], command: Any, kind: str, point: str, before: Any
) -> tuple[dict[str, Any], Any]:
    state: dict[str, Any] = {
        "hits": [],
        "revisions": {},
        "participants": [],
        "selected_audit": None,
    }
    project, entity = ctx["project"], ctx["object"]
    _, _, wanted_event = command_input(ctx, kind)

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
        external_changes = {
            t: external[t] for t in before if t not in BOOKKEEPING and external[t] != before[t]
        }
        staged: list[dict[str, Any]] = []
        for participant in state["participants"]:
            participant_table = metadata.tables[participant["table"]]
            match: dict[str, Any] = participant["identity"]
            found = list(
                connection.execute(
                    select(participant_table).where(
                        *(participant_table.c[k] == v for k, v in match.items())
                    )
                ).mappings()
            )
            staged.append({**participant, "rows": [dict(row) for row in found]})
        state["hits"].append(
            {
                "point": point,
                "table": table_name,
                "identity": identity,
                "rows": [dict(r) for r in rows],
                "participants": staged,
                "revisions": dict(state["revisions"]),
                "same_engine": connection.engine is runtime.ledger.engine,
                "in_transaction": connection.in_transaction(),
                "external_domain_unchanged": not external_changes,
                "external_domain_changes": external_changes,
            }
        )
        assert rows and connection.in_transaction() and connection.engine is runtime.ledger.engine
        assert all(participant["rows"] for participant in staged)
        assert not external_changes, "HOLD_DEFECT: intermediate domain publication"

    mapping = {
        "insert_snapshot": ("entity_snapshots", "snapshot_id"),
        "insert_revision": ("semantic_revisions", "revision_id"),
        "insert_receipt": ("receipts", "receipt_id"),
        "add_object": ("decision_objects", "object_revision_id"),
        "append_audit": ("object_audit", "audit_id"),
        "add_relation": ("object_relations", "relation_revision_id"),
        "add_attention": ("object_attention", "attention_revision_id"),
        "add": ("relations", "relation_id"),
    }

    def wrap(name: str, original: Any) -> Any:
        def call(instance: Any, *args: Any, **kwargs: Any) -> Any:
            record = args[0]
            selected = active()
            if name == "set_head":
                selected = (
                    selected and args[0] == project and args[1] == "DECISION_OBJECT:" + entity
                )
                table, identity = (
                    "working_heads",
                    {"project_id": project, "aggregate_key": args[1], "revision_digest": args[2]},
                )
            else:
                selected = selected and record.project_id == project
                if name in {"insert_snapshot", "insert_revision"}:
                    selected = (
                        selected
                        and record.entity_id == entity
                        and record.entity_type.value == "DECISION_OBJECT"
                    )
                elif name == "insert_receipt":
                    selected = selected and bool(
                        set(record.subject_refs) & state["revisions"].keys()
                    )
                elif name == "add_relation":
                    selected = selected and record.source_object_id == entity
                elif name == "add":
                    selected = selected and record.source_ref == "OBJECT:" + entity
                else:
                    selected = selected and record.object_id == entity
                table, field = mapping[name]
                identity = {"project_id": project, field: getattr(record, field)}
            if (
                selected
                and name == "append_audit"
                and point == "proposal_insert"
                and record.event_type == wanted_event
            ):
                state["selected_audit"] = record.model_dump(mode="json")
            result = original(instance, *args, **kwargs)
            if not selected:
                return result
            if name == "insert_revision":
                state["revisions"][record.revision_id] = record.reason
            if name in {"add_object", "add_relation", "add_attention", "add"}:
                state["participants"].append({"table": table, "identity": identity})
            target = point == name
            if name == "append_audit":
                target = target and record.event_type == wanted_event
                if kind == "relation_remove":
                    target = target and record.payload.get("reason") == "84 ending relation"
            if target:
                with read_connection(runtime.ledger.engine) as connection:
                    if name in FRAME_POINTS[:4]:
                        assert connection is instance._connection
                    observe(connection, table, identity)
                raise RuntimeError("84 post-write fault: " + kind + ":" + point)
            return result

        return call

    for owner, names in (
        (SqliteLedgerTransaction, FRAME_POINTS[:4]),
        (
            SqliteDecisionObjectStore,
            ("add_object", "append_audit", "add_relation", "add_attention"),
        ),
        (SqliteDependencyGraph, ("add",)),
    ):
        for name in names:
            patch.setattr(owner, name, wrap(name, getattr(owner, name)))

    def after_sql(
        connection: Any, _cursor: Any, _statement: str, _params: Any, context: Any, _many: bool
    ) -> None:
        selected = state["selected_audit"]
        if (
            point != "proposal_insert"
            or not active()
            or selected is None
            or not context.isinsert
            or context.compiled is None
        ):
            return
        if context.compiled.statement.table.name != "object_audit":
            return
        params = context.compiled_parameters
        if (
            len(params) != 1
            or params[0].get("audit_id") != selected["audit_id"]
            or params[0].get("project_id") != project
        ):
            return
        observe(
            connection,
            "object_audit",
            {
                "project_id": project,
                "object_id": entity,
                "audit_id": selected["audit_id"],
                "event_type": wanted_event,
            },
        )
        raise RuntimeError("84 proposal INSERT before commit: " + kind)

    return state, after_sql


@pytest.mark.parametrize("kind,point", CASES)
async def test_mutation_post_write_failure_is_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, point: str
) -> None:
    runtime, ctx = await prepare(tmp_path, kind)
    key = "84-fault-" + kind + "-" + point
    method, payload, _ = command_input(ctx, kind)
    command = request(method, key, payload)
    reads = expected_reads(ctx, kind)
    before = snapshot(runtime.ledger.engine)
    save(
        key + "-source-expectation",
        {
            "expected_reads": reads,
            "current": ctx["current"],
            "request": command.model_dump(mode="json"),
            "basis": "source-read-set-expectations.json",
        },
    )
    try:
        registry = cast(Any, vars(runtime.bus)["_registry"])
        actual = registry.resolve(method)
        binding = actual.__module__ + "." + actual.__qualname__
        assert binding.startswith("thoth.application.commands.objects_full.DecisionObjectHandlers.")
        with monkeypatch.context() as patch:
            state, callback = install_fault(patch, runtime, ctx, command, kind, point, before)
            if point == "proposal_insert":
                event.listen(runtime.ledger.engine, "after_cursor_execute", callback)
            try:
                failed = await runtime.bus.dispatch(command)
            finally:
                if point == "proposal_insert":
                    event.remove(runtime.ledger.engine, "after_cursor_execute", callback)
        after = snapshot(runtime.ledger.engine)
        evidence: dict[str, Any] = {
            "request": command.model_dump(mode="json"),
            "response": failed.model_dump(mode="json"),
            "expected_reads": reads,
            "actual_callable": binding,
            "fault": state,
            "before": before,
            "after": after,
        }
        operations = [json.loads(row) for row in after["operations"]]
        operation = next(row for row in operations if row["idempotency_key"] == key)
        binding_row = next(
            json.loads(row)
            for row in after["operation_resource_bindings"]
            if json.loads(row)["operation_id"] == operation["operation_id"]
        )
        actual_reads = sorted(
            (use["resource_ref"], use["capability"])
            for use in json.loads(binding_row["content_json"])["resource_uses"]
        )
        evidence["observed_resource_uses"] = actual_reads
        save(key, evidence)
        assert len(state["hits"]) == 1, "HOLD_CONTRACT_MISMATCH: expected target hit1"
        required = {
            "profile": {"decision_objects"},
            "facet": {"decision_objects"},
            "work": {"decision_objects"},
            "relation_add": {"decision_objects", "object_relations", "relations"},
            "relation_remove": {"decision_objects", "object_relations"},
            "revalidate": {"decision_objects", "object_attention"},
            "ack": {"decision_objects", "object_attention"},
            "clear": {"decision_objects", "object_attention"},
        }.get(kind, set())
        assert required.issubset({p["table"] for p in state["hits"][0]["participants"]})
        if kind in {"split", "merge"}:
            assert state["selected_audit"]["payload"]["applied"] is False
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
        assert await read_object(reopened, ctx, key + "-preserved") == ctx["current"]
        recovered = value(await reopened.bus.dispatch(request(method, key + "-retry", payload)))
        if kind in {"split", "merge"}:
            assert recovered[kind + "_proposal"]["applied"] is False
            assert reopened.ledger.read_heads(ctx["project"]) == {
                json.loads(row)["aggregate_key"]: json.loads(row)["revision_digest"]
                for row in before["working_heads"]
                if json.loads(row)["project_id"] == ctx["project"]
            }
            assert await read_object(reopened, ctx, key + "-proposal-read") == ctx["current"]
        else:
            record = recovered["object"]
            assert await read_object(reopened, ctx, key + "-recovered") == record
            assert (
                reopened.ledger.read_heads(ctx["project"])["DECISION_OBJECT:" + ctx["object"]]
                == record["revision_digest"]
            )
            assert record["object_revision_id"] in recovered["commit"]["receipt"]["subject_refs"]
        save(
            key,
            {
                **evidence,
                "verdict": "PASS",
                "reopened": restarted,
                "replay_exact": True,
                "recovered": recovered,
            },
        )
    finally:
        reopened.close()
