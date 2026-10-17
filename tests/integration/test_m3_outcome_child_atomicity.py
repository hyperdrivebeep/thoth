"""Outcome child rows and the standalone follow-up audit (94b).

Same evidence rules as 94a/92b: each target writer runs the original first and then raises, with hit
count 1, the target row and every earlier participant staged on the ambient connection, no
intermediate domain publication on a separate connection, whole-DB equality afterwards (Execution
rows included), an exact replay, a reopened runtime and a new-key retry that publishes. Read basis:
outputs/refactor-step94-20261015/source-read-set-expectations-94b.json (written before the faults).

Attribution, change set and impact rows are written by add_attribution, add_change_set and
add_impact without a ledger revision, head or receipt (OUTCOME-CHILD-LEDGER-SCOPE, OPEN); impact
writes no audit either (OUTCOME-IMPACT-NO-AUDIT, OPEN). This file verifies the current contract.

outcome/followup/generate writes exactly one audit row through OutcomeService.audit, called by the
handler outside any service transaction, so that statement commits on its own
(OUTCOME-FOLLOWUP-STANDALONE-AUDIT, step 0 of 94b, step0-followup-observation-94b.json). A failure
inside the statement rolls it back and is verified below; an exception raised after the commit
would leave the row, which no code path in the handler can produce (it only returns), so that
case is recorded, not injected.
"""

from __future__ import annotations

import json
from itertools import pairwise
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import Connection, event, func, select
from tests.atomicity.harness import assert_phase_delta, failed_command_allowances, snapshot
from tests.atomicity.outcome_helpers import prepare_outcome
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import request, value

from thoth.adapters.storage.outcome import SqliteOutcomeStore
from thoth.adapters.storage.schema import metadata
from thoth.adapters.storage.sqlite import SqliteLedgerTransaction
from thoth.adapters.storage.transaction import read_connection
from thoth.domain.canonical import head_set_digest
from thoth.protocol.deferred import current_operation

OUT = Path(__file__).resolve().parents[2] / "outputs/refactor-step94-20261015"
BOOKKEEPING = {"operations", "idempotency_keys", "events", "operation_resource_bindings"}
OWNERS = {"L": SqliteLedgerTransaction, "O": SqliteOutcomeStore}
TABLES = {
    ("L", "insert_snapshot"): ("entity_snapshots", "snapshot_id"),
    ("L", "insert_revision"): ("semantic_revisions", "revision_id"),
    ("L", "insert_receipt"): ("receipts", "receipt_id"),
    ("O", "add_assessment"): ("outcome_assessments", "assessment_revision_id"),
    ("O", "add_attribution"): ("outcome_attributions", "attribution_assessment_id"),
    ("O", "add_change_set"): ("outcome_change_sets", "outcome_change_set_id"),
    ("O", "add_impact"): ("outcome_impacts", "impact_assessment_id"),
    ("O", "append_audit"): ("outcome_audit", "audit_id"),
}
WRITERS = (*TABLES, ("L", "set_head"))
COUNTED = (
    "outcome_series",
    "outcome_assessments",
    "outcome_attributions",
    "outcome_change_sets",
    "outcome_impacts",
    "outcome_audit",
)
ROUTES = {
    "attribution": "outcome/attribution/assess",
    "change_set": "outcome/changeSet/propose",
    "followup": "outcome/followup/generate",
    "impact": "outcome/impact/propose",
}
HANDLER = "thoth.application.commands.outcomes_full.OutcomeHandlers."
HANDLER_METHODS = {
    "attribution": "attribution_assess",
    "change_set": "change_set_propose",
    "followup": "followup_generate",
    "impact": "impact_propose",
}
# New rows a recovered request adds, in the order of COUNTED.
DELTAS = {
    "attribution": (0, 1, 1, 0, 0, 1),
    "change_set": (0, 0, 0, 1, 0, 1),
    "followup": (0, 0, 0, 0, 0, 1),
    "impact": (0, 0, 0, 0, 1, 0),
}
Step = tuple[str, str, str | None]
ORDERS: dict[str, tuple[Step, ...]] = {
    # The child row is staged first; the assessment revision that references it follows.
    "attribution": (
        ("O", "add_attribution", None),
        ("L", "insert_snapshot", None),
        ("L", "insert_revision", None),
        ("L", "set_head", None),
        ("L", "insert_receipt", None),
        ("O", "add_assessment", None),
        ("O", "append_audit", "outcome/attributionUpdated"),
    ),
    "change_set": (
        ("O", "add_change_set", None),
        ("O", "append_audit", "outcome/changeSetProposed"),
    ),
    "impact": (("O", "add_impact", None),),
}
# (kind, label, owner, writer, event, occurrence of that writer/event inside the request)
CASES: list[tuple[str, str, str, str, str | None, int]] = [
    ("attribution", "add_attribution", "O", "add_attribution", None, 1),
    ("attribution", "first_ledger_write", "L", "insert_snapshot", None, 1),
    ("attribution", "add_assessment", "O", "add_assessment", None, 1),
    (
        "attribution",
        "audit_attributionUpdated",
        "O",
        "append_audit",
        "outcome/attributionUpdated",
        1,
    ),
    ("change_set", "add_change_set", "O", "add_change_set", None, 1),
    ("change_set", "audit_changeSetProposed", "O", "append_audit", "outcome/changeSetProposed", 1),
    ("impact", "add_impact", "O", "add_impact", None, 1),
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


async def prepare(path: Path, kind: str) -> tuple[Any, dict[str, Any], str, dict[str, Any]]:
    """An assessed OutcomeSeries (planned_execution_ref None) and the request of the given kind."""

    runtime, ctx = await prepare_outcome(path, create_series=True, assess=True)
    assessment = ctx["assessment"]
    base = {
        "project_id": ctx["project"],
        "outcome_assessment_id": assessment["outcome_assessment_id"],
    }
    payload: dict[str, Any]
    if kind == "attribution":
        payload = {
            **base,
            "attribution_method": "CONTRIBUTION_ANALYSIS",
            "contextual_factor_refs": [],
            "evidence_refs": ctx["spans"],
        }
    elif kind == "change_set":
        payload = {
            **base,
            "proposed_entity_changes": {},
            "impact_policy_ref": "policy:candidate-only",
            "expected_project_head_set": head_set_digest(runtime.ledger.read_heads(ctx["project"])),
        }
    elif kind == "followup":
        payload = {**base, "follow_up_scope": "Resolve current limitations"}
    else:
        payload = {
            "project_id": ctx["project"],
            "outcome_series_id": ctx["series"]["outcome_series_id"],
            "broader_window": "later follow-up",
            "impact_profile_ref": "EXPERIMENT_LEARNING_OUTCOME",
            "evidence_refs": ctx["spans"],
            "attribution_design_ref": "TEMPORAL_ONLY",
        }
    return runtime, ctx, ROUTES[kind], payload


def expected_reads(ctx: dict[str, Any], kind: str) -> tuple[str, ...]:
    """Source-defined top-level reads: see source-read-set-expectations-94b.json."""

    if kind == "impact":
        series = ctx["series"]
        refs = [ref for refs in series["observation_refs_by_phase"].values() for ref in refs]
        return tuple(sorted({revision(series["revision_digest"]), *refs}))
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


def row_counts(runtime: Any, project: str) -> tuple[int, ...]:
    result: list[int] = []
    with read_connection(runtime.ledger.engine) as connection:
        for name in COUNTED:
            table = metadata.tables[name]
            result.append(
                int(
                    connection.execute(
                        select(func.count()).select_from(table).where(table.c.project_id == project)
                    ).scalar_one()
                )
            )
    return tuple(result)


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
                    raise RuntimeError("94b post-write fault: " + ":".join(map(str, target)))
            return result

        return call_original

    for short, name in WRITERS:
        owner = OWNERS[short]
        patch.setattr(owner, name, wrap(short, name, getattr(owner, name)))
    return state


def published_assessment(runtime: Any, record: dict[str, Any], commit: dict[str, Any]) -> None:
    heads = runtime.ledger.read_heads(record["project_id"])
    assert heads["OUTCOME:" + record["outcome_assessment_id"]] == record["revision_digest"]
    assert record["assessment_revision_id"] in commit["receipt"]["subject_refs"]


async def check_published(runtime: Any, project: str, kind: str, result: dict[str, Any]) -> None:
    heads = runtime.ledger.read_heads(project)
    if kind == "attribution":
        published_assessment(runtime, result["assessment"], result["commit"])
        child = result["attribution"]
        assert result["assessment"]["attribution_ref"] == child["attribution_assessment_id"]
        read = await call(
            runtime,
            "outcome/attribution/read",
            "94b-read-attribution",
            {
                "project_id": project,
                "attribution_assessment_id": child["attribution_assessment_id"],
            },
        )
        assert read["attribution"] == child
        child_id = child["attribution_assessment_id"]
    elif kind == "change_set":
        child = result["change_set"]
        read = await call(
            runtime,
            "outcome/changeSet/read",
            "94b-read-change-set",
            {"project_id": project, "outcome_change_set_id": child["outcome_change_set_id"]},
        )
        assert read["change_set"] == child and result["current_state_mutated"] is False
        child_id = child["outcome_change_set_id"]
    elif kind == "impact":
        child = result["impact"]
        read = await call(
            runtime,
            "outcome/impact/read",
            "94b-read-impact",
            {"project_id": project, "impact_assessment_id": child["impact_assessment_id"]},
        )
        assert read["impact"] == child
        child_id = child["impact_assessment_id"]
    else:
        assert "digest" in result["candidates"]
        return
    # The current contract: a child row has no ledger head of its own (OUTCOME-CHILD-LEDGER-SCOPE).
    assert not any(child_id in key for key in heads), heads


async def views(runtime: Any, ctx: dict[str, Any], phase: str) -> dict[str, Any]:
    project = ctx["project"]
    series = ctx["series"]
    assessment = ctx["assessment"]
    series_base = {"project_id": project, "outcome_series_id": series["outcome_series_id"]}
    assessment_base = {
        "project_id": project,
        "outcome_assessment_id": assessment["outcome_assessment_id"],
    }
    return {
        "heads": dict(runtime.ledger.read_heads(project)),
        "assessment": await call(runtime, "outcome/read", phase + "-assessment", assessment_base),
        "assessment_audit": await call(
            runtime, "outcome/audit/read", phase + "-aaudit", assessment_base
        ),
        "series_audit": await call(runtime, "outcome/audit/read", phase + "-saudit", series_base),
    }


@pytest.mark.parametrize(
    "kind,label,owner,writer,event,nth", CASES, ids=[f"{c[0]}-{c[1]}" for c in CASES]
)
async def test_outcome_child_post_write_failure_is_atomic(
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
    key = "94b-fault-" + kind + "-" + label
    command = request(method, key, payload)
    reads = expected_reads(ctx, kind)
    views_before = await views(runtime, ctx, "94b-before")
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
        assert await views(reopened, ctx, "94b-preserved") == views_before
        recovered = value(await reopened.bus.dispatch(request(method, key + "-retry", payload)))
        await check_published(reopened, ctx["project"], kind, recovered)
        counts_after = row_counts(reopened, ctx["project"])
        delta = tuple(a - b for a, b in zip(counts_after, counts_before, strict=True))
        assert delta == DELTAS[kind], ("HOLD_DEFECT: duplicated or missing rows", delta)
        save(key, {**evidence, "verdict": "PASS", "replay_exact": True, "recovered_delta": delta})
    finally:
        reopened.close()


async def test_followup_writes_one_standalone_audit_row(tmp_path: Path) -> None:
    """Step 0 classification, kept as a test: the only domain row is the audit row."""

    runtime, ctx, method, payload = await prepare(tmp_path, "followup")
    statements: list[str] = []

    def statement(
        conn: Connection, cursor: Any, text: str, params: Any, context: Any, many: bool
    ) -> None:
        head = text.lstrip().split("(")[0].strip().upper()
        if head.startswith(("INSERT", "UPDATE", "DELETE")):
            statements.append(" ".join(head.split()[:3]))

    engine = runtime.ledger.engine
    try:
        before = snapshot(engine)
        event.listen(engine, "before_cursor_execute", statement)
        try:
            result = await call(runtime, method, "94b-followup-normal", payload)
        finally:
            event.remove(engine, "before_cursor_execute", statement)
        after = snapshot(engine)
        # The bus, not Outcome, writes operation bookkeeping and the event-stream checkpoint.
        bus_tables = {
            "INSERT INTO OPERATIONS",
            "INSERT INTO IDEMPOTENCY_KEYS",
            "INSERT INTO EVENTS",
            "UPDATE OPERATIONS SET",
            "INSERT INTO OPERATION_RESOURCE_BINDINGS",
            "INSERT INTO CHECKPOINTS",
            "UPDATE CHECKPOINTS SET",
        }
        domain = sorted(item for item in statements if item not in bus_tables)
        assert domain == ["INSERT INTO OUTCOME_AUDIT"], domain
        changed = sorted(
            name
            for name in before
            if name not in BOOKKEEPING | {"checkpoints"} and before[name] != after[name]
        )
        assert changed == ["outcome_audit"], changed
        assert "digest" in result["candidates"]
        await check_published(runtime, ctx["project"], "followup", result)
    finally:
        runtime.close()


async def test_followup_audit_statement_failure_is_atomic(tmp_path: Path) -> None:
    runtime, ctx, method, payload = await prepare(tmp_path, "followup")
    key = "94b-fault-followup-statement"
    command = request(method, key, payload)
    reads = expected_reads(ctx, "followup")
    views_before = await views(runtime, ctx, "94b-before")
    counts_before = row_counts(runtime, ctx["project"])
    before = snapshot(runtime.ledger.engine)
    hits: list[bool] = []

    def after_sql(
        connection: Connection,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        many: bool,
    ) -> None:
        if statement.lstrip().upper().startswith("INSERT INTO OUTCOME_AUDIT "):
            hits.append(connection.in_transaction())
            raise RuntimeError("94b follow-up audit statement fault")

    try:
        event.listen(runtime.ledger.engine, "after_cursor_execute", after_sql)
        try:
            failed = await runtime.bus.dispatch(command)
        finally:
            event.remove(runtime.ledger.engine, "after_cursor_execute", after_sql)
        after = snapshot(runtime.ledger.engine)
        assert hits == [True], hits
        assert failed.error is not None
        assert all(before[t] == after[t] for t in before if t not in BOOKKEEPING), (
            "HOLD_DEFECT: failed follow-up published domain rows"
        )
        assert_phase_delta(
            before,
            after,
            failed_command_allowances(runtime.ledger.engine, command, expected_reads=reads),
        )
        save(
            key,
            {
                "case": ["followup", "statement"],
                "error": failed.model_dump(mode="json").get("error"),
                "expected_reads": reads,
            },
        )
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path)
    try:
        assert snapshot(reopened.ledger.engine) == after
        replay = await reopened.bus.dispatch(command)
        assert replay.error == failed.error
        assert snapshot(reopened.ledger.engine) == after
        assert await views(reopened, ctx, "94b-preserved") == views_before
        recovered = value(await reopened.bus.dispatch(request(method, key + "-retry", payload)))
        await check_published(reopened, ctx["project"], "followup", recovered)
        counts_after = row_counts(reopened, ctx["project"])
        delta = tuple(a - b for a, b in zip(counts_after, counts_before, strict=True))
        assert delta == DELTAS["followup"], delta
    finally:
        reopened.close()


async def change(
    runtime: Any, ctx: dict[str, Any], kind: str, key: str, payload: dict[str, Any]
) -> dict[str, Any]:
    command = request(ROUTES[kind], key, payload)
    result = value(await runtime.bus.dispatch(command))
    after = snapshot(runtime.ledger.engine)
    assert value(await runtime.bus.dispatch(command)) == result
    assert snapshot(runtime.ledger.engine) == after
    await check_published(runtime, ctx["project"], kind, result)
    return result


async def test_outcome_child_chain_control(tmp_path: Path) -> None:
    runtime, ctx, _method, attribution_payload = await prepare(tmp_path / "chain", "attribution")
    try:
        project = ctx["project"]
        assessment_id = ctx["assessment"]["outcome_assessment_id"]
        attributed = await change(
            runtime, ctx, "attribution", "94b-control-attribution", attribution_payload
        )
        base = {"project_id": project, "outcome_assessment_id": assessment_id}
        await change(
            runtime,
            ctx,
            "change_set",
            "94b-control-change-set",
            {
                **base,
                "proposed_entity_changes": {},
                "impact_policy_ref": "policy:candidate-only",
                "expected_project_head_set": head_set_digest(runtime.ledger.read_heads(project)),
            },
        )
        await change(
            runtime,
            ctx,
            "followup",
            "94b-control-followup",
            {**base, "follow_up_scope": "Resolve current limitations"},
        )
        await change(
            runtime,
            ctx,
            "impact",
            "94b-control-impact",
            {
                "project_id": project,
                "outcome_series_id": ctx["series"]["outcome_series_id"],
                "broader_window": "later follow-up",
                "impact_profile_ref": "EXPERIMENT_LEARNING_OUTCOME",
                "evidence_refs": ctx["spans"],
                "attribution_design_ref": "TEMPORAL_ONLY",
            },
        )
        # The assessment has two revisions (assess, attribution update); child rows add no revision.
        revisions = runtime.ledger.read_revisions(project, "OUTCOME", assessment_id)
        for earlier, later in pairwise(revisions):
            assert later.parent_revision_digests == (earlier.revision_digest,)
        assert len(revisions) == 2
        assert (
            attributed["assessment"]["supersedes_revision_digest"]
            == ctx["assessment"]["revision_digest"]
        )
        ctx["assessment"] = attributed["assessment"]
        events = [
            item["event_type"]
            for item in (await call(runtime, "outcome/audit/read", "94b-control-audit", base))[
                "records"
            ]
        ]
        assert events == [
            "outcome/assessed",
            "outcome/attributionUpdated",
            "outcome/changeSetProposed",
            "outcome/followupGenerated",
        ]
        series_events = [
            item["event_type"]
            for item in (
                await call(
                    runtime,
                    "outcome/audit/read",
                    "94b-control-saudit",
                    {
                        "project_id": project,
                        "outcome_series_id": ctx["series"]["outcome_series_id"],
                    },
                )
            )["records"]
        ]
        assert not any("mpact" in name for name in series_events), series_events
        snap = snapshot(runtime.ledger.engine)
        assert snap["plan_executions"] == () and snap["step_execution_attempts"] == ()
        expected = await views(runtime, ctx, "94b-live")
        # The snapshot is taken after the reads: reads add operation bookkeeping rows (see 92b/94a).
        persisted = snapshot(runtime.ledger.engine)
        save(
            "control-outcome-child-chain",
            {"assessment_revisions": len(revisions), "audit_events": events, "views": expected},
        )
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path / "chain")
    try:
        assert snapshot(reopened.ledger.engine) == persisted
        assert await views(reopened, ctx, "94b-reopen") == expected
    finally:
        reopened.close()
