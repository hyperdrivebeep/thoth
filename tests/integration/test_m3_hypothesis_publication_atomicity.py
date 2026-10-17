"""Direct Hypothesis publication gates; not whole-owner atomicity certification."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from tests.atomicity.harness import assert_phase_delta, failed_command_allowances, snapshot
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_a03_portfolio_atomicity import portfolio_input
from tests.integration.test_a03_storage_atomicity import create_hypothesis, setup

from thoth.adapters.storage.hypothesis import SqliteHypothesisStore
from thoth.adapters.storage.schema import metadata
from thoth.adapters.storage.sqlite import SqliteLedgerTransaction
from thoth.adapters.storage.transaction import read_connection
from thoth.apps.runtime import AppRuntime
from thoth.domain.canonical import head_set_digest
from thoth.protocol.deferred import current_operation

_OUT = Path(__file__).resolve().parents[2] / "outputs/refactor-step69-20261014"
_LEDGER_POINTS = ("insert_snapshot", "insert_revision", "set_head", "insert_receipt")


def _save(name: str, data: dict[str, Any]) -> None:
    _OUT.mkdir(parents=True, exist_ok=True)
    (_OUT / name).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


async def _prepare(path: Path, kind: str) -> tuple[AppRuntime, dict[str, Any]]:
    runtime, ctx = await setup(path)
    try:
        if kind == "portfolio":
            payload = await portfolio_input(runtime, ctx)
            ctx.update(
                payload=payload,
                entity=payload["portfolio_id"],
                method="hypothesis/portfolio/compose",
                reads=(),
            )
        else:
            current = await create_hypothesis(runtime, ctx)
            ctx.update(
                current=current,
                entity=current["hypothesis_id"],
                method="hypothesis/appraise",
                reads=(f"revision:{current['revision_digest']}", *ctx["spans"]),
                payload={
                    "project_id": ctx["project"],
                    "hypothesis_id": current["hypothesis_id"],
                    "evidence_refs": ctx["spans"],
                    "test_assessment_refs": [],
                    "appraisal_scope": {"workstream": "integration"},
                },
            )
        # Read-only runtime inventory; no replacement of dispatch or its registry.
        registry = vars(runtime.bus)["_registry"]
        handler = registry.resolve(ctx["method"])
        ctx["actual_callable"] = handler.__module__ + "." + handler.__qualname__
        expected = "portfolio_compose" if kind == "portfolio" else "appraise"
        assert ctx["actual_callable"] == (
            "thoth.application.commands.hypotheses_full.HypothesisHandlers." + expected
        )
        return runtime, ctx
    except BaseException:
        runtime.close()
        raise


async def _readback(runtime: AppRuntime, ctx: dict[str, Any], key: str) -> dict[str, Any]:
    is_portfolio = ctx["method"] == "hypothesis/portfolio/compose"
    method = "hypothesis/portfolio/read" if is_portfolio else "hypothesis/appraisal/read"
    field = "portfolio_id" if is_portfolio else "hypothesis_id"
    response = await runtime.bus.dispatch(
        request(method, key, {"project_id": ctx["project"], field: ctx["entity"]})
    )
    return response.model_dump(mode="json")


def _check_result(runtime: AppRuntime, ctx: dict[str, Any], result: dict[str, Any]) -> None:
    portfolio = ctx["method"] == "hypothesis/portfolio/compose"
    record = result["portfolio" if portfolio else "hypothesis"]
    revision = record["portfolio_revision_id" if portfolio else "hypothesis_revision_id"]
    heads = runtime.ledger.read_heads(ctx["project"])
    assert heads["HYPOTHESIS:" + ctx["entity"]] == record["revision_digest"]
    receipt = result["commit"]["receipt"]
    assert revision in receipt["subject_refs"]
    assert receipt["after_head_set_digest"] == head_set_digest(heads)
    assert any(
        r.receipt_id == receipt["receipt_id"] for r in runtime.ledger.read_receipts(ctx["project"])
    )
    if not portfolio:
        assert result["appraisal"]["appraisal"] == "INCONCLUSIVE"
        assert result["appraisal"]["substantive_update_applied"] is False
        for field in ("empirical_appraisal", "development_stage"):
            assert record[field] == ctx["current"][field]


@pytest.mark.parametrize("count", [0, 1])
async def test_public_portfolio_minimum_rejects_without_domain_writes(
    tmp_path: Path, count: int
) -> None:
    runtime, ctx = await _prepare(tmp_path, "portfolio")
    try:
        ctx["payload"]["hypothesis_ids"] = ctx["payload"]["hypothesis_ids"][:count]
        ctx["payload"]["unknown_reserve"].update(
            alternatives_considered=["Unobserved mechanism"],
            next_checks=["Collect an independent measurement"],
        )
        before = snapshot(runtime.ledger.engine)
        command = request(ctx["method"], "public-minimum", ctx["payload"])
        response = await runtime.bus.dispatch(command)
        after = snapshot(runtime.ledger.engine)
        assert response.error is not None and response.error.code == -32602
        allowed = failed_command_allowances(runtime.ledger.engine, command)
        assert_phase_delta(before, after, allowed)
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path)
    try:
        restarted = snapshot(reopened.ledger.engine)
        assert restarted == after
        assert_phase_delta(before, restarted, allowed)
        _save(
            f"public-minimum-{count}.json",
            {
                "request": command.model_dump(mode="json"),
                "response": response.model_dump(mode="json"),
                "before": before,
                "after": after,
                "reopened": restarted,
                "verdict": "PASS",
            },
        )
    finally:
        reopened.close()


@pytest.mark.parametrize("kind,count", [("portfolio", 2), ("appraise", 0)])
async def test_partial_publication_control_and_replay(
    tmp_path: Path, kind: str, count: int
) -> None:
    runtime, ctx = await _prepare(tmp_path, kind)
    try:
        if kind == "portfolio":
            ctx["payload"]["hypothesis_ids"] = ctx["payload"]["hypothesis_ids"][:count]
        command = request(ctx["method"], "control", ctx["payload"])
        created = value(await runtime.bus.dispatch(command))
        _check_result(runtime, ctx, created)
        if kind == "portfolio":
            assert len(created["portfolio"]["hypothesis_refs"]) == count
            assert created["portfolio"]["abstention_state"] == "AVAILABLE"
        after = snapshot(runtime.ledger.engine)
        assert value(await runtime.bus.dispatch(command)) == created
        assert snapshot(runtime.ledger.engine) == after
        conflict_payload = dict(ctx["payload"])
        conflict_payload["unknown_reserve" if kind == "portfolio" else "appraisal_scope"] = {
            "reason": "different payload"
        }
        assert (
            await runtime.bus.dispatch(request(ctx["method"], "control", conflict_payload))
        ).error is not None
        assert snapshot(runtime.ledger.engine) == after
        live = await _readback(runtime, ctx, "control-read")
        assert live["error"] is None
        after_read = snapshot(runtime.ledger.engine)
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path)
    try:
        assert snapshot(reopened.ledger.engine) == after_read
        restored = await _readback(reopened, ctx, "control-reopened")
        assert restored["error"] is None
        assert restored["result"]["value"] == live["result"]["value"]
        _save(
            f"control-{kind}-{count}.json",
            {"created": created, "readback": restored, "snapshot_equal": True},
        )
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "kind,point",
    [(kind, point) for kind in ("portfolio", "appraise") for point in _LEDGER_POINTS]
    + [("appraise", "add_hypothesis"), ("appraise", "append_audit")],
)
async def test_publication_failure_rolls_back_whole_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, point: str
) -> None:
    runtime, ctx = await _prepare(tmp_path, kind)
    key = f"failure-{kind}-{point}"
    command = request(ctx["method"], key, ctx["payload"])
    entity, project = ctx["entity"], ctx["project"]
    aggregate = "HYPOTHESIS:" + entity
    reason = "hypothesis/portfolioUpdated" if kind == "portfolio" else "hypothesis/appraisalUpdated"
    hits: list[dict[str, Any]] = []
    staged: set[str] = set()
    before = snapshot(runtime.ledger.engine)
    owner = SqliteLedgerTransaction if point in _LEDGER_POINTS else SqliteHypothesisStore
    original: Any = getattr(owner, point)
    original_revision = SqliteLedgerTransaction.insert_revision

    def active() -> bool:
        operation = current_operation.get()
        return operation is not None and (
            operation.method,
            operation.idempotency_key,
            operation.project_id,
        ) == (ctx["method"], key, project)

    def target_revision(record: Any) -> bool:
        return (
            record.project_id == project and record.entity_id == entity and record.reason == reason
        )

    def observe_revision(transaction: SqliteLedgerTransaction, record: Any) -> None:
        if active() and target_revision(record):
            staged.add(record.revision_id)
        original_revision(transaction, record)

    def fail(instance: Any, *args: Any, **kwargs: Any) -> Any:
        record = args[0]
        matching = active()
        if matching:
            if point == "set_head":
                matching = args[0] == project and args[1] == aggregate
            elif point == "insert_receipt":
                matching = record.project_id == project and bool(
                    staged.intersection(record.subject_refs)
                )
            elif point == "insert_revision":
                matching = target_revision(record)
                if matching:
                    staged.add(record.revision_id)
            elif point == "insert_snapshot":
                matching = (
                    record.project_id == project
                    and record.entity_id == entity
                    and record.entity_type.value == "HYPOTHESIS"
                )
            elif point == "append_audit":
                matching = (
                    record.project_id == project
                    and record.hypothesis_id == entity
                    and record.event_type == reason
                )
            else:
                matching = record.project_id == project and record.hypothesis_id == entity
        result = original(instance, *args, **kwargs)
        if not matching:
            return result
        if point == "set_head":
            table_name, identity = (
                "working_heads",
                {"project_id": project, "aggregate_key": aggregate, "revision_digest": args[2]},
            )
        else:
            table_name, field = {
                "insert_snapshot": ("entity_snapshots", "snapshot_id"),
                "insert_revision": ("semantic_revisions", "revision_id"),
                "insert_receipt": ("receipts", "receipt_id"),
                "add_hypothesis": ("hypothesis_records", "hypothesis_revision_id"),
                "append_audit": ("hypothesis_audit", "audit_id"),
            }[point]
            identity = {field: getattr(record, field), "project_id": project}
        table = metadata.tables[table_name]
        with read_connection(runtime.ledger.engine) as connection:
            assert connection.in_transaction()
            if point in _LEDGER_POINTS:
                assert connection is instance._connection
            else:
                assert instance._engine is runtime.ledger.engine
            rows = (
                connection.execute(
                    select(table).where(*(table.c[k] == v for k, v in identity.items()))
                )
                .mappings()
                .all()
            )
            assert len(rows) == 1
            hits.append(
                {
                    "point": point,
                    "identity": identity,
                    "row": dict(rows[0]),
                    "connection": id(connection),
                    "staged_revisions": sorted(staged),
                }
            )
        raise RuntimeError("M3 hypothesis post-write fault: " + point)

    try:
        with monkeypatch.context() as patch:
            if point != "insert_revision":
                patch.setattr(SqliteLedgerTransaction, "insert_revision", observe_revision)
            patch.setattr(owner, point, fail)
            response = await runtime.bus.dispatch(command)
        after = snapshot(runtime.ledger.engine)
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path)
    try:
        restarted = snapshot(reopened.ledger.engine)
        delta = {}
        for table in before.keys() | after.keys():
            left, right = Counter(before.get(table, ())), Counter(after.get(table, ()))
            if left != right:
                delta[table] = {
                    "added": [json.loads(r) for r in (right - left).elements()],
                    "removed": [json.loads(r) for r in (left - right).elements()],
                }
        evidence = {
            "actual_callable": ctx["actual_callable"],
            "response": response.model_dump(mode="json"),
            "request": command.model_dump(mode="json"),
            "hits": hits,
            "before": before,
            "after": after,
            "reopened": restarted,
            "delta": delta,
            "expected_reads": ctx["reads"],
        }
        _save(key + ".json", evidence)
        assert len(hits) == 1 and response.error is not None
        assert restarted == after
        allowed = failed_command_allowances(
            reopened.ledger.engine, command, expected_reads=ctx["reads"]
        )
        assert_phase_delta(before, restarted, allowed)
        readback = await _readback(reopened, ctx, key + "-read")
        evidence["failed_readback"] = readback
        evidence["observation_snapshot"] = snapshot(reopened.ledger.engine)
        _save(key + ".json", evidence)
        assert (readback["error"] is not None) == (kind == "portfolio")
        if kind == "appraise":
            assert readback["result"]["value"]["appraisals"] == []
        recovered = value(
            await reopened.bus.dispatch(request(ctx["method"], key + "-retry", ctx["payload"]))
        )
        _check_result(reopened, ctx, recovered)
        evidence.update(recovered=recovered, verdict="PASS")
        _save(key + ".json", evidence)
    finally:
        reopened.close()
