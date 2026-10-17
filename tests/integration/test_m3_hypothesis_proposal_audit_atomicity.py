"""Proposal audit failure is injected inside its INSERT transaction, not after commit."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import event, select
from tests.atomicity.harness import assert_phase_delta, failed_command_allowances, snapshot
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_a03_portfolio_atomicity import portfolio_input
from tests.integration.test_a03_storage_atomicity import setup

from thoth.adapters.storage.hypothesis import SqliteHypothesisStore
from thoth.adapters.storage.schema import hypothesis_audit
from thoth.protocol.deferred import current_operation

_OUT = Path(__file__).resolve().parents[2] / "outputs/refactor-step75-20261014"


@pytest.mark.parametrize("kind", ["split", "merge"])
async def test_proposal_audit_insert_failure_does_not_apply_or_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    runtime, ctx = await setup(tmp_path)
    project = ctx["project"]
    try:
        portfolio = await portfolio_input(runtime, ctx)
        records = [
            value(
                await runtime.bus.dispatch(
                    request(
                        "hypothesis/read",
                        f"before-{i}",
                        {"project_id": project, "hypothesis_id": h},
                    )
                )
            )["hypothesis"]
            for i, h in enumerate(portfolio["hypothesis_ids"])
        ]
        method, key = f"hypothesis/{kind}/propose", "75-proposal-" + kind
        payload: dict[str, Any] = {
            "project_id": project,
            "evidence_refs": ctx["spans"],
            "rationale": "Preview only; preserve current hypotheses",
        }
        if kind == "split":
            payload.update(
                hypothesis_id=records[0]["hypothesis_id"],
                expected_revision_digest=records[0]["revision_digest"],
                subhypotheses=[
                    {"statement": "Clock source differs"},
                    {"statement": "Timestamp conversion differs"},
                ],
            )
            reads = ("revision:" + records[0]["revision_digest"],)
        else:
            payload.update(
                hypothesis_ids=[r["hypothesis_id"] for r in records],
                expected_revision_digests=[r["revision_digest"] for r in records],
                field_mapping={"statement": "retain-both"},
            )
            reads = tuple(sorted("revision:" + r["revision_digest"] for r in records))
        command = request(method, key, payload)
        before = snapshot(runtime.ledger.engine)
        heads = dict(runtime.ledger.read_heads(project))
        original = SqliteHypothesisStore.append_audit
        selected: dict[str, Any] = {}
        hits: list[dict[str, Any]] = []
        wanted_event = "hypothesis/" + kind + "Proposed"

        def active() -> bool:
            operation = current_operation.get()
            return operation is not None and (
                operation.method,
                operation.idempotency_key,
                operation.project_id,
            ) == (method, key, project)

        def capture(store: SqliteHypothesisStore, record: Any) -> None:
            if (
                active()
                and record.project_id == project
                and record.hypothesis_id == records[0]["hypothesis_id"]
                and record.event_type == wanted_event
            ):
                selected.update(record.model_dump(mode="json"))
            original(store, record)

        def after_sql(
            connection: Any,
            _cursor: Any,
            statement: str,
            _parameters: Any,
            context: Any,
            _many: bool,
        ) -> None:
            if not active() or not selected or not context.isinsert or context.compiled is None:
                return
            if context.compiled.statement.table.name != "hypothesis_audit":
                return
            parameters = context.compiled_parameters
            if (
                len(parameters) != 1
                or parameters[0].get("audit_id") != selected["audit_id"]
                or parameters[0].get("project_id") != project
            ):
                return
            row = (
                connection.execute(
                    select(hypothesis_audit).where(
                        hypothesis_audit.c.audit_id == selected["audit_id"],
                        hypothesis_audit.c.project_id == project,
                        hypothesis_audit.c.hypothesis_id == records[0]["hypothesis_id"],
                        hypothesis_audit.c.event_type == wanted_event,
                    )
                )
                .mappings()
                .one_or_none()
            )
            hits.append(
                {
                    "statement": statement,
                    "audit_id": selected["audit_id"],
                    "row": None if row is None else dict(row),
                    "in_transaction": connection.in_transaction(),
                    "same_engine": connection.engine is runtime.ledger.engine,
                }
            )
            raise RuntimeError("M3_PROPOSAL_INSERT_BEFORE_COMMIT:" + kind)

        with monkeypatch.context() as patch:
            patch.setattr(SqliteHypothesisStore, "append_audit", capture)
            event.listen(runtime.ledger.engine, "after_cursor_execute", after_sql)
            try:
                failed = await runtime.bus.dispatch(command)
            finally:
                event.remove(runtime.ledger.engine, "after_cursor_execute", after_sql)
        after = snapshot(runtime.ledger.engine)
        evidence: dict[str, Any] = {
            "kind": kind,
            "request": command.model_dump(mode="json"),
            "response": failed.model_dump(mode="json"),
            "selected_audit": selected,
            "hits": hits,
            "before": before,
            "after": after,
        }
        _OUT.mkdir(parents=True, exist_ok=True)
        (_OUT / f"proposal-{kind}.json").write_text(
            json.dumps(evidence, indent=2, default=str) + "\n", encoding="utf-8"
        )
        assert failed.error is not None and len(hits) == 1
        assert hits[0]["row"] is not None and hits[0]["in_transaction"] and hits[0]["same_engine"]
        assert (
            selected["payload"]["applied"] is False
            and selected["payload"]["owner_namespace"] == "REVISION"
        )
        allowed = failed_command_allowances(runtime.ledger.engine, command, expected_reads=reads)
        assert_phase_delta(before, after, allowed)
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path)
    try:
        evidence["reopened"] = snapshot(reopened.ledger.engine)
        assert evidence["reopened"] == after
        created = value(await reopened.bus.dispatch(request(method, key + "-retry", payload)))
        proposal = created[kind + "_proposal"]
        assert proposal["applied"] is False and proposal["owner_namespace"] == "REVISION"
        assert dict(reopened.ledger.read_heads(project)) == heads
        for i, record in enumerate(records):
            read = value(
                await reopened.bus.dispatch(
                    request(
                        "hypothesis/read",
                        f"after-{i}",
                        {"project_id": project, "hypothesis_id": record["hypothesis_id"]},
                    )
                )
            )
            assert read["hypothesis"] == record
        audit = value(
            await reopened.bus.dispatch(
                request(
                    "hypothesis/audit/read",
                    "audit-proposal",
                    {"project_id": project, "hypothesis_id": records[0]["hypothesis_id"]},
                )
            )
        )
        selected_rows = [r for r in audit["records"] if r["event_type"] == wanted_event]
        assert len(selected_rows) == 1 and selected_rows[0]["payload"] == proposal
        evidence.update(
            recovered=created,
            audit=selected_rows,
            heads_unchanged=True,
            hypotheses_unchanged=True,
            verdict="PASS",
        )
        (_OUT / f"proposal-{kind}.json").write_text(
            json.dumps(evidence, indent=2, default=str) + "\n", encoding="utf-8"
        )
    finally:
        reopened.close()
