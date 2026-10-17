"""Outcome series and assessment revisions (94a): real post-write fault injection.

Same evidence rules as 92a/92b: each target writer runs the original first and then raises, with hit
count 1, the target row and every earlier participant staged on the ambient connection, no
intermediate domain publication on a separate connection, whole-DB equality afterwards (Execution
rows included), an exact replay, a reopened runtime and a new-key retry that publishes. Read basis:
outputs/refactor-step94-20261015/source-read-set-expectations-94a.json (written before the faults).

Series and assessment rows are published through OutcomeService._persist_series and
_persist_assessment: ledger commit, owner row, owner audit. Child rows (attribution, change set,
impact) and the standalone follow-up audit are 94b. OUTCOME-CHILD-LEDGER-SCOPE is recorded in the
owner map; this file verifies the current contract only.
"""

from __future__ import annotations

import json
from itertools import pairwise
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import func, select
from tests.atomicity.harness import assert_phase_delta, failed_command_allowances, snapshot
from tests.atomicity.outcome_helpers import prepare_outcome
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import request, value

from thoth.adapters.storage.outcome import SqliteOutcomeStore
from thoth.adapters.storage.schema import metadata
from thoth.adapters.storage.sqlite import SqliteLedgerTransaction
from thoth.adapters.storage.transaction import read_connection
from thoth.protocol.deferred import current_operation

OUT = Path(__file__).resolve().parents[2] / "outputs/refactor-step94-20261015"
BOOKKEEPING = {"operations", "idempotency_keys", "events", "operation_resource_bindings"}
OWNERS = {"L": SqliteLedgerTransaction, "O": SqliteOutcomeStore}
TABLES = {
    ("L", "insert_snapshot"): ("entity_snapshots", "snapshot_id"),
    ("L", "insert_revision"): ("semantic_revisions", "revision_id"),
    ("L", "insert_receipt"): ("receipts", "receipt_id"),
    ("O", "add_series"): ("outcome_series", "series_revision_id"),
    ("O", "add_assessment"): ("outcome_assessments", "assessment_revision_id"),
    ("O", "append_audit"): ("outcome_audit", "audit_id"),
}
WRITERS = (*TABLES, ("L", "set_head"))
ROUTES = {
    "series_create": "outcome/series/create",
    "observation_link": "outcome/observation/link",
    "assess": "outcome/assess",
    "reassess": "outcome/reassess",
}
HANDLER_METHODS = {
    "series_create": "series_create",
    "observation_link": "observation_link",
    "assess": "assess",
    "reassess": "reassess",
}
HANDLER = "thoth.application.commands.outcomes_full.OutcomeHandlers."
# New rows a recovered request adds: (series rows, assessment rows, audit rows).
DELTAS = {
    "series_create": (1, 0, 1),
    "observation_link": (1, 0, 1),
    "assess": (1, 1, 2),
    "reassess": (0, 1, 1),
}
Step = tuple[str, str, str | None]


def persist(owner_row: str, event: str) -> tuple[Step, ...]:
    """OutcomeService._persist_series/_persist_assessment: ledger commit, owner row, owner audit."""

    return (
        ("L", "insert_snapshot", None),
        ("L", "insert_revision", None),
        ("L", "set_head", None),
        ("L", "insert_receipt", None),
        ("O", owner_row, None),
        ("O", "append_audit", event),
    )


ORDERS: dict[str, tuple[Step, ...]] = {
    "series_create": persist("add_series", "outcome/seriesCreated"),
    "observation_link": persist("add_series", "outcome/observationLinked"),
    # The assessment is published first, then the series that lists it: two heads, one event type.
    "assess": (
        *persist("add_assessment", "outcome/assessed"),
        *persist("add_series", "outcome/assessed"),
    ),
    "reassess": persist("add_assessment", "outcome/reassessed"),
}
# (kind, label, owner, writer, event, occurrence of that writer/event inside the request)
CASES: list[tuple[str, str, str, str, str | None, int]] = [
    ("series_create", "insert_snapshot", "L", "insert_snapshot", None, 1),
    ("series_create", "insert_revision", "L", "insert_revision", None, 1),
    ("series_create", "set_head", "L", "set_head", None, 1),
    ("series_create", "insert_receipt", "L", "insert_receipt", None, 1),
    ("series_create", "add_series", "O", "add_series", None, 1),
    ("series_create", "audit_seriesCreated", "O", "append_audit", "outcome/seriesCreated", 1),
    ("observation_link", "add_series", "O", "add_series", None, 1),
    (
        "observation_link",
        "audit_observationLinked",
        "O",
        "append_audit",
        "outcome/observationLinked",
        1,
    ),
    ("assess", "add_assessment", "O", "add_assessment", None, 1),
    ("assess", "audit_assessed_assessment", "O", "append_audit", "outcome/assessed", 1),
    ("assess", "second_set_head", "L", "set_head", None, 2),
    ("assess", "add_series", "O", "add_series", None, 1),
    ("assess", "audit_assessed_series", "O", "append_audit", "outcome/assessed", 2),
    ("reassess", "add_assessment", "O", "add_assessment", None, 1),
    ("reassess", "audit_reassessed", "O", "append_audit", "outcome/reassessed", 1),
]


def save(name: str, data: dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / (name + ".json")).write_text(
        json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8"
    )


async def call(runtime: Any, method: str, key: str, payload: dict[str, Any]) -> dict[str, Any]:
    return value(await runtime.bus.dispatch(request(method, key, payload)))


def revision(digest: str) -> str:
    return "revision:" + digest


def expected_prefix(kind: str, owner: str, writer: str, event: str | None, nth: int) -> list[Step]:
    order = ORDERS[kind]
    seen = 0
    for index, (own, name, label) in enumerate(order):
        if (own, name) == (owner, writer) and (event is None or label == event):
            seen += 1
            if seen == nth:
                return list(order[: index + 1])
    raise AssertionError("target is not in the source-derived writer order")


def link_payload(ctx: dict[str, Any], key_suffix: str = "") -> dict[str, Any]:
    series = ctx["series"]
    return {
        "project_id": ctx["project"],
        "outcome_series_id": series["outcome_series_id"],
        "assessment_phase": "INTERIM",
        "observation_refs": ctx["spans"],
        "completeness": "PARTIAL",
        "evidence_refs": ctx["spans"],
        "expected_series_revision": series["revision"],
    }


def assess_payload(ctx: dict[str, Any]) -> dict[str, Any]:
    series = ctx["series"]
    return {
        "project_id": ctx["project"],
        "outcome_series_id": series["outcome_series_id"],
        "assessment_phase": "INTERIM",
        "profile_version": 1,
        "observation_refs": ctx["spans"],
        "comparator_refs": ctx["spans"],
        "assumptions": [],
        "expected_series_revision": series["revision"],
    }


async def prepare(path: Path, kind: str) -> tuple[Any, dict[str, Any], str, dict[str, Any]]:
    if kind == "series_create":
        runtime, ctx = await prepare_outcome(path, create_series=False)
        return runtime, ctx, ROUTES[kind], dict(ctx["series_input"])
    runtime, ctx = await prepare_outcome(path, create_series=True, assess=kind == "reassess")
    try:
        if kind == "observation_link":
            payload = link_payload(ctx)
        elif kind == "assess":
            linked = await call(
                runtime, "outcome/observation/link", "94a-prepare-link", link_payload(ctx)
            )
            ctx["series"] = linked["series"]
            payload = assess_payload(ctx)
        else:
            assessment = ctx["assessment"]
            payload = {
                "project_id": ctx["project"],
                "outcome_assessment_id": assessment["outcome_assessment_id"],
                "expected_revision_digest": assessment["revision_digest"],
                "new_evidence_refs": ctx["spans"],
                "reason": "94a reassess",
            }
        return runtime, ctx, ROUTES[kind], payload
    except BaseException:
        runtime.close()
        raise


def expected_reads(ctx: dict[str, Any], kind: str) -> tuple[str, ...]:
    """Source-defined top-level reads: see source-read-set-expectations-94a.json."""

    if kind == "series_create":
        return ()
    if kind == "observation_link":
        return (revision(ctx["series"]["revision_digest"]),)
    if kind == "assess":
        return tuple(sorted({revision(ctx["series"]["revision_digest"]), *ctx["spans"]}))
    assessment = ctx["assessment"]
    return tuple(
        sorted(
            {
                revision(assessment["revision_digest"]),
                *assessment["actual_observation_refs"],
                *assessment["comparator_refs"],
            }
        )
    )


def row_counts(runtime: Any, project: str) -> tuple[int, int, int]:
    result: list[int] = []
    with read_connection(runtime.ledger.engine) as connection:
        for name in ("outcome_series", "outcome_assessments", "outcome_audit"):
            table = metadata.tables[name]
            result.append(
                int(
                    connection.execute(
                        select(func.count()).select_from(table).where(table.c.project_id == project)
                    ).scalar_one()
                )
            )
    return (result[0], result[1], result[2])


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
        # 2026-10-15 (see 92b): set_head updates the working_heads row of an aggregate in place,
        # so an earlier head write of the same aggregate is superseded by the later one.
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
                    raise RuntimeError("94a post-write fault: " + ":".join(map(str, target)))
            return result

        return call_original

    for short, name in WRITERS:
        owner = OWNERS[short]
        patch.setattr(owner, name, wrap(short, name, getattr(owner, name)))
    return state


def published(runtime: Any, record: dict[str, Any], commit: dict[str, Any], id_key: str) -> None:
    heads = runtime.ledger.read_heads(record["project_id"])
    assert heads["OUTCOME:" + record[id_key]] == record["revision_digest"]
    revision_id = record.get("series_revision_id") or record["assessment_revision_id"]
    assert revision_id in commit["receipt"]["subject_refs"]


def check_published(runtime: Any, kind: str, result: dict[str, Any]) -> None:
    if kind in {"series_create", "observation_link"}:
        published(runtime, result["series"], result["commit"], "outcome_series_id")
    elif kind == "assess":
        assessment_commit, series_commit = result["commits"]
        published(runtime, result["assessment"], assessment_commit, "outcome_assessment_id")
        published(runtime, result["series"], series_commit, "outcome_series_id")
        assert result["assessment"]["outcome_assessment_id"] in result["series"]["assessment_refs"]
    else:
        published(runtime, result["assessment"], result["commit"], "outcome_assessment_id")


async def views(runtime: Any, ctx: dict[str, Any], phase: str) -> dict[str, Any]:
    project = ctx["project"]
    result: dict[str, Any] = {"heads": dict(runtime.ledger.read_heads(project))}
    result["series_list"] = await call(
        runtime, "outcome/series/list", phase + "-list", {"project_id": project}
    )
    series = ctx.get("series")
    if series is not None:
        base = {"project_id": project, "outcome_series_id": series["outcome_series_id"]}
        result["series_audit"] = await call(runtime, "outcome/audit/read", phase + "-saudit", base)
    assessment = ctx.get("assessment")
    if assessment is not None:
        base = {"project_id": project, "outcome_assessment_id": assessment["outcome_assessment_id"]}
        result["assessment"] = await call(runtime, "outcome/read", phase + "-assessment", base)
        result["assessment_audit"] = await call(
            runtime, "outcome/audit/read", phase + "-aaudit", base
        )
    return result


@pytest.mark.parametrize(
    "kind,label,owner,writer,event,nth", CASES, ids=[f"{c[0]}-{c[1]}" for c in CASES]
)
async def test_outcome_revision_post_write_failure_is_atomic(
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
    key = "94a-fault-" + kind + "-" + label
    command = request(method, key, payload)
    reads = expected_reads(ctx, kind)
    views_before = await views(runtime, ctx, "94a-before")
    counts_before = row_counts(runtime, ctx["project"])
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
        # Every non-bookkeeping table, Execution tables included, equals the state before.
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
        assert await views(reopened, ctx, "94a-preserved") == views_before
        recovered = value(await reopened.bus.dispatch(request(method, key + "-retry", payload)))
        check_published(reopened, kind, recovered)
        counts_after = row_counts(reopened, ctx["project"])
        delta = tuple(a - b for a, b in zip(counts_after, counts_before, strict=True))
        assert delta == DELTAS[kind], ("HOLD_DEFECT: duplicated or missing rows", delta)
        save(key, {**evidence, "verdict": "PASS", "replay_exact": True, "recovered_delta": delta})
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
    check_published(runtime, kind, result)
    return result


def check_chain(runtime: Any, project: str, entity_id: str) -> list[Any]:
    revisions = runtime.ledger.read_revisions(project, "OUTCOME", entity_id)
    for earlier, later in pairwise(revisions):
        assert later.parent_revision_digests == (earlier.revision_digest,)
    return list(revisions)


async def test_outcome_revision_chain_control(tmp_path: Path) -> None:
    runtime, ctx = await prepare_outcome(tmp_path, create_series=False)
    try:
        created = await change(
            runtime,
            ctx,
            "series_create",
            "outcome/series/create",
            "94a-control-create",
            ctx["series_input"],
        )
        ctx["series"] = created["series"]
        linked = await change(
            runtime,
            ctx,
            "observation_link",
            "outcome/observation/link",
            "94a-control-link",
            link_payload(ctx),
        )
        ctx["series"] = linked["series"]
        assessed = await change(
            runtime, ctx, "assess", "outcome/assess", "94a-control-assess", assess_payload(ctx)
        )
        ctx["series"], ctx["assessment"] = assessed["series"], assessed["assessment"]
        reassessed = await change(
            runtime,
            ctx,
            "reassess",
            "outcome/reassess",
            "94a-control-reassess",
            {
                "project_id": ctx["project"],
                "outcome_assessment_id": ctx["assessment"]["outcome_assessment_id"],
                "expected_revision_digest": ctx["assessment"]["revision_digest"],
                "new_evidence_refs": ctx["spans"],
                "reason": "94a control",
            },
        )
        series_revisions = check_chain(runtime, ctx["project"], ctx["series"]["outcome_series_id"])
        assessment_revisions = check_chain(
            runtime, ctx["project"], ctx["assessment"]["outcome_assessment_id"]
        )
        assert len(series_revisions) == 3 and len(assessment_revisions) == 2
        assert (
            reassessed["assessment"]["supersedes_revision_digest"]
            == (ctx["assessment"]["revision_digest"])
        )
        ctx["assessment"] = reassessed["assessment"]
        # No Execution row is involved: Outcome is execution-optional here.
        snap = snapshot(runtime.ledger.engine)
        assert snap["plan_executions"] == () and snap["step_execution_attempts"] == ()
        expected = await views(runtime, ctx, "94a-live")
        # The snapshot is taken after the reads: reads add operation bookkeeping rows (see 92b).
        persisted = snapshot(runtime.ledger.engine)
        save(
            "control-outcome-revision-chain",
            {
                "series_revisions": len(series_revisions),
                "assessment_revisions": len(assessment_revisions),
                "views": expected,
            },
        )
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path)
    try:
        assert snapshot(reopened.ledger.engine) == persisted
        assert await views(reopened, ctx, "94a-reopen") == expected
    finally:
        reopened.close()
