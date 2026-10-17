"""Public mutation paths share the frozen Hypothesis writer and preserve history."""

from __future__ import annotations

import json
import sys
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from types import FrameType
from typing import Any

import pytest
from sqlalchemy import select
from tests.atomicity.harness import assert_phase_delta, failed_command_allowances, snapshot
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_a03_storage_atomicity import create_hypothesis, creation, setup
from tests.integration.test_hypothesis_prediction_outcome_cycle import prepare_measurement

from thoth.adapters.storage.hypothesis import SqliteHypothesisStore
from thoth.adapters.storage.schema import metadata
from thoth.adapters.storage.transaction import read_connection
from thoth.application.commands.hypothesis_inputs import CreateInput, GenerateInput
from thoth.protocol.deferred import current_operation
from thoth.protocol.jsonrpc import JsonRpcRequest

_OUT = Path(__file__).resolve().parents[2] / "outputs/refactor-step73-20261014"
_SERVICE = "thoth.application.services.hypothesis_service"
_COMMIT = "thoth.application.services.revision_service"
_SQLITE = "thoth.adapters.storage.sqlite"
_STORE = "thoth.adapters.storage.hypothesis"
_PATH = [
    _SERVICE + "._persist_hypothesis",
    _SERVICE + "._commit_record",
    _COMMIT + ".commit",
    _SQLITE + ".insert_snapshot",
    _SQLITE + ".insert_revision",
    _SQLITE + ".set_head",
    _SQLITE + ".insert_receipt",
    _STORE + ".add_hypothesis",
    _STORE + ".append_audit",
]


def _save(name: str, evidence: dict[str, Any]) -> None:
    _OUT.mkdir(parents=True, exist_ok=True)
    (_OUT / name).write_text(json.dumps(evidence, indent=2, default=str) + "\n", encoding="utf-8")


@contextmanager
def _calls(command: JsonRpcRequest) -> Generator[list[dict[str, Any]]]:
    """Read Python call events; never replace the private persistence methods."""
    calls: list[dict[str, Any]] = []
    revisions: dict[str, str] = {}

    def observe(frame: FrameType, event: str, _arg: Any) -> None:
        if event != "call":
            return
        operation = current_operation.get()
        if operation is None or (
            operation.method,
            operation.idempotency_key,
            operation.project_id,
        ) != (
            command.method,
            command.params.meta.idempotency_key,
            command.params.input["project_id"],
        ):
            return
        symbol = str(frame.f_globals.get("__name__")) + "." + frame.f_code.co_name
        if symbol not in _PATH:
            return
        data = frame.f_locals
        entities: list[str] = []
        if symbol in {_SERVICE + "._persist_hypothesis", _SERVICE + "._commit_record"}:
            record = data["record"]
            entities = [record.hypothesis_id]
            revisions[record.hypothesis_revision_id] = record.hypothesis_id
        elif symbol == _COMMIT + ".commit":
            for staged in data["changeset"].staged_revisions:
                if staged.revision.entity_type.value == "HYPOTHESIS":
                    entities.append(staged.revision.entity_id)
                    revisions[staged.revision.revision_id] = staged.revision.entity_id
        elif symbol in {_SQLITE + ".insert_snapshot", _SQLITE + ".insert_revision"}:
            record = data["snapshot" if symbol.endswith("insert_snapshot") else "revision"]
            if record.entity_type.value == "HYPOTHESIS":
                entities = [record.entity_id]
        elif symbol == _SQLITE + ".set_head":
            if data["aggregate_key"].startswith("HYPOTHESIS:"):
                entities = [data["aggregate_key"].split(":", 1)[1]]
        elif symbol == _SQLITE + ".insert_receipt":
            entities = [revisions[r] for r in data["receipt"].subject_refs if r in revisions]
        else:
            entities = [data["value"].hypothesis_id]
        for entity in entities:
            calls.append({"symbol": symbol, "entity": entity, "source": frame.f_code.co_filename})

    previous = sys.getprofile()
    sys.setprofile(observe)
    try:
        yield calls
    finally:
        sys.setprofile(previous)


def _assert_path(calls: list[dict[str, Any]], entity: str) -> None:
    assert [c["symbol"] for c in calls if c["entity"] == entity] == _PATH


def _generate(ctx: dict[str, Any], count: int) -> dict[str, Any]:
    assert len(ctx["spans"]) >= count
    payload: dict[str, Any] = {
        "project_id": ctx["project"],
        "object_id": ctx["object"],
        "question": "Why did the clock drift?",
        "evidence_scope": ctx["spans"][:count],
        "intent_hints": ["DIAGNOSTIC_CAUSAL"],
        "generation_policy_ref": "generation:bounded",
    }
    assert len(GenerateInput.model_validate(payload).evidence_scope) == count
    return payload


async def _read(runtime: Any, project: str, record: dict[str, Any], key: str) -> dict[str, Any]:
    response = value(
        await runtime.bus.dispatch(
            request(
                "hypothesis/read",
                key,
                {"project_id": project, "hypothesis_id": record["hypothesis_id"]},
            )
        )
    )
    assert response["hypothesis"] == record
    assert (
        runtime.ledger.read_heads(project)["HYPOTHESIS:" + record["hypothesis_id"]]
        == record["revision_digest"]
    )
    return response


@pytest.mark.parametrize("count", [0, 1, 2])
async def test_generate_public_empty_and_partial_evidence(tmp_path: Path, count: int) -> None:
    runtime, ctx = await setup(tmp_path)
    try:
        payload = _generate(ctx, count)
        command = request("hypothesis/generate", "73-generate-control", payload)
        with _calls(command) as calls:
            result = value(await runtime.bus.dispatch(command))
        records = result["hypotheses"]
        assert len(records) == len(result["commits"]) == count + 1
        assert result["unknown_reserve"] == records[-1]["hypothesis_id"]
        for index, record in enumerate(records):
            _assert_path(calls, record["hypothesis_id"])
            assert record["portfolio_id"] == "portfolio:" + ctx["object"]
            assert (
                record["hypothesis_revision_id"]
                in result["commits"][index]["receipt"]["subject_refs"]
            )
            if index == count:
                assert record["primary_intent"] == "EXPLORATORY"
                assert (
                    record["evidence_basis"] == "UNKNOWN_RESERVE" and record["evidence_refs"] == []
                )
            else:
                assert record["evidence_refs"] == [ctx["spans"][index]]
        after = snapshot(runtime.ledger.engine)
        assert value(await runtime.bus.dispatch(command)) == result
        assert snapshot(runtime.ledger.engine) == after
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path)
    try:
        assert snapshot(reopened.ledger.engine) == after
        for index, record in enumerate(records):
            await _read(reopened, ctx["project"], record, f"read-{index}")
        _save(
            f"generate-{count}.json",
            {
                "input_schema": GenerateInput.model_json_schema()["properties"]["evidence_scope"],
                "response": result,
                "calls": calls,
                "reopened_equal": True,
                "verdict": "PASS",
            },
        )
    finally:
        reopened.close()


async def test_draft_and_reference_appraisal_use_shared_writer(tmp_path: Path) -> None:
    runtime, ctx = await setup(tmp_path)
    try:
        payload: dict[str, Any] = {**creation(ctx), "scope": {}, "evidence_refs": []}
        CreateInput.model_validate(payload)
        command = request("hypothesis/create", "73-draft", payload)
        with _calls(command) as calls:
            result = value(await runtime.bus.dispatch(command))
        record = result["hypothesis"]
        assert (
            record["development_stage"] == "DRAFT" and record["empirical_appraisal"] == "UNASSESSED"
        )
        assert record["gateway_results"]["source_cutoff_provenance"] == "INCOMPLETE"
        _assert_path(calls, record["hypothesis_id"])
        reference = request(
            "hypothesis/appraise",
            "73-reference69",
            {
                "project_id": ctx["project"],
                "hypothesis_id": record["hypothesis_id"],
                "evidence_refs": [],
                "test_assessment_refs": [],
                "appraisal_scope": {"workstream": "integration"},
            },
        )
        with _calls(reference) as reference_calls:
            appraised = value(await runtime.bus.dispatch(reference))
        assert appraised["appraisal"]["substantive_update_applied"] is False
        _assert_path(reference_calls, record["hypothesis_id"])
        after = snapshot(runtime.ledger.engine)
        _save(
            "shared-writer-reference.json",
            {
                "draft": result,
                "draft_calls": calls,
                "reference_appraise": appraised,
                "reference_calls": reference_calls,
                "expected_path": _PATH,
            },
        )
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path)
    try:
        assert snapshot(reopened.ledger.engine) == after
        await _read(reopened, ctx["project"], appraised["hypothesis"], "read-reference")
    finally:
        reopened.close()


async def test_revise_invalidates_real_references_without_rewriting_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, sandbox, project, thread, _contract = await prepare_measurement(tmp_path, monkeypatch)
    try:
        measured = value(
            await runtime.bus.dispatch(
                request("thread/input", "measure", {"project_id": project, "thread_id": thread})
            )
        )
        hypothesis_id = measured["r2_closed_loop"]["test_lifecycle"]["predictions"][0][
            "hypothesis_id"
        ]
        current = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/read",
                    "before",
                    {"project_id": project, "hypothesis_id": hypothesis_id},
                )
            )
        )["hypothesis"]
        assert current["prediction_refs"] and current["test_refs"]
        old = snapshot(runtime.ledger.engine)
        command = request(
            "hypothesis/revise",
            "73-invalidate",
            {
                "project_id": project,
                "hypothesis_id": hypothesis_id,
                "expected_revision_digest": current["revision_digest"],
                "patch": {
                    "statement": "A revised explanation requires a new discriminating measurement"
                },
                "evidence_refs": current["evidence_refs"],
                "reason": "Clarify the explanatory claim",
            },
        )
        with _calls(command) as calls:
            revised = value(await runtime.bus.dispatch(command))
        record = revised["hypothesis"]
        _assert_path(calls, hypothesis_id)
        assert record["prediction_refs"] == record["test_refs"] == []
        assert record["empirical_appraisal"] == "UNASSESSED"
        assert set(current["prediction_refs"] + current["test_refs"] + ["APPRAISALS"]) <= set(
            record["invalidated_refs"]
        )
        assert revised["invalidated_predictions_tests_appraisals"] == record["invalidated_refs"]
        after = snapshot(runtime.ledger.engine)
        for table in (
            "hypothesis_predictions",
            "hypothesis_test_bindings",
            "hypothesis_appraisals",
            "research_test_assessments",
        ):
            assert old[table] == after[table]
        for table in ("entity_snapshots", "semantic_revisions", "hypothesis_records"):
            assert set(old[table]) <= set(after[table])
        historical = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/read",
                    "history",
                    {
                        "project_id": project,
                        "hypothesis_id": hypothesis_id,
                        "revision_digest": current["revision_digest"],
                    },
                )
            )
        )["hypothesis"]
        assert historical == current
        await _read(runtime, project, record, "current")
        assert len(sandbox.seen_specs) == 1
        _save(
            "derived-invalidation.json",
            {
                "before": current,
                "after": revised,
                "history": historical,
                "calls": calls,
                "old_rows_preserved": True,
                "verdict": "PASS",
            },
        )
    finally:
        runtime.close()


@pytest.mark.parametrize("kind", ["create", "generate", "revise", "intent", "causal"])
async def test_public_mutation_late_audit_failure_is_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    runtime, ctx = await setup(tmp_path)
    project = ctx["project"]
    current = None
    payload: dict[str, Any]
    if kind == "create":
        method, payload, event_type, reads = (
            "hypothesis/create",
            creation(ctx),
            "hypothesis/created",
            (),
        )
    elif kind == "generate":
        method, payload, event_type, reads = (
            "hypothesis/generate",
            _generate(ctx, 2),
            "hypothesis/created",
            tuple(ctx["spans"][:2]),
        )
    else:
        current = await create_hypothesis(runtime, ctx)
        payload = {
            "project_id": project,
            "hypothesis_id": current["hypothesis_id"],
            "expected_revision_digest": current["revision_digest"],
            "evidence_refs": ctx["spans"],
            "reason": "Scoped public mutation",
        }
        method = {
            "revise": "hypothesis/revise",
            "intent": "hypothesis/intent/update",
            "causal": "hypothesis/causal/update",
        }[kind]
        event_type = "hypothesis/intentChanged" if kind == "intent" else "hypothesis/updated"
        reads = tuple(sorted({f"revision:{current['revision_digest']}", *ctx["spans"]}))
        if kind == "revise":
            payload["patch"] = {"statement": "A revised source-grounded clock explanation"}
        elif kind == "intent":
            payload.update(
                primary_intent="PREDICTIVE",
                secondary_intents=[],
                intent_profile_refs=["intent-profile:predictive:1"],
            )
        else:
            payload["causal_patch"] = {
                "primary_locus": "INTERFACE_OR_INTEGRATION",
                "contributing_loci": ["MEASUREMENT_OR_DATA"],
                "causal_depth": "PROXIMATE",
                "mechanism": "clock-source mismatch",
                "validity_blockers": [],
            }
    command = request(method, "73-fault-" + kind, payload)
    before = snapshot(runtime.ledger.engine)
    records: dict[str, Any] = {}
    hits: list[dict[str, Any]] = []
    add, audit = SqliteHypothesisStore.add_hypothesis, SqliteHypothesisStore.append_audit

    def active() -> bool:
        operation = current_operation.get()
        return operation is not None and (
            operation.method,
            operation.idempotency_key,
            operation.project_id,
        ) == (method, command.params.meta.idempotency_key, project)

    def observe_add(store: SqliteHypothesisStore, record: Any) -> None:
        add(store, record)
        if active() and record.project_id == project:
            records[record.hypothesis_id] = record

    def fail_audit(store: SqliteHypothesisStore, record: Any) -> None:
        audit(store, record)
        item = records.get(record.hypothesis_id)
        if (
            not active()
            or item is None
            or record.project_id != project
            or record.event_type != event_type
            or record.payload.get("revision_digest") != item.revision_digest
        ):
            return
        if current is not None and item.hypothesis_id != current["hypothesis_id"]:
            return
        if kind == "generate" and not (
            item.primary_intent == "EXPLORATORY"
            and item.evidence_basis == "UNKNOWN_RESERVE"
            and not item.evidence_refs
        ):
            return
        observed: dict[str, Any] = {}
        with read_connection(runtime.ledger.engine) as connection:
            for table_name, field, identifier in (
                ("hypothesis_records", "hypothesis_revision_id", item.hypothesis_revision_id),
                ("semantic_revisions", "revision_id", item.hypothesis_revision_id),
                ("working_heads", "aggregate_key", "HYPOTHESIS:" + item.hypothesis_id),
                ("hypothesis_audit", "audit_id", record.audit_id),
                ("research_identities", "owner_revision", item.revision_digest),
            ):
                table = metadata.tables[table_name]
                row = (
                    connection.execute(
                        select(table).where(
                            table.c.project_id == project, table.c[field] == identifier
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                observed[table_name] = None if row is None else dict(row)
            canonical = observed["semantic_revisions"]
            snapshot_table = metadata.tables["entity_snapshots"]
            snapshot_row = (
                None
                if canonical is None
                else connection.execute(
                    select(snapshot_table).where(
                        snapshot_table.c.project_id == project,
                        snapshot_table.c.snapshot_id == canonical["snapshot_id"],
                    )
                )
                .mappings()
                .one_or_none()
            )
            observed["entity_snapshots"] = None if snapshot_row is None else dict(snapshot_row)
            receipts = metadata.tables["receipts"]
            observed["receipts"] = [
                dict(r)
                for r in connection.execute(
                    select(receipts).where(receipts.c.project_id == project)
                ).mappings()
                if item.hypothesis_revision_id in json.loads(r["payload_json"])["subject_refs"]
            ]
            staged = metadata.tables["hypothesis_records"]
            observed["batch_rows"] = [
                dict(r)
                for r in connection.execute(
                    select(staged).where(
                        staged.c.project_id == project, staged.c.hypothesis_id.in_(list(records))
                    )
                ).mappings()
            ]
            revision_ids = {r.hypothesis_revision_id for r in records.values()}
            for table_name, field, identifiers in (
                ("semantic_revisions", "revision_id", revision_ids),
                ("working_heads", "aggregate_key", {"HYPOTHESIS:" + k for k in records}),
                (
                    "research_identities",
                    "owner_revision",
                    {r.revision_digest for r in records.values()},
                ),
            ):
                table = metadata.tables[table_name]
                observed["batch_" + table_name] = [
                    dict(r)
                    for r in connection.execute(
                        select(table).where(
                            table.c.project_id == project, table.c[field].in_(identifiers)
                        )
                    ).mappings()
                ]
            observed["batch_receipts"] = [
                dict(r)
                for r in connection.execute(
                    select(receipts).where(receipts.c.project_id == project)
                ).mappings()
                if revision_ids.intersection(json.loads(r["payload_json"])["subject_refs"])
            ]
            hits.append(
                {
                    "entity": item.hypothesis_id,
                    "digest": item.revision_digest,
                    "event_type": record.event_type,
                    "transaction_active": connection.in_transaction(),
                    "rows": observed,
                }
            )
        raise RuntimeError("M3_HYPOTHESIS_AUDIT_FAILURE:" + kind)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(SqliteHypothesisStore, "add_hypothesis", observe_add)
            patch.setattr(SqliteHypothesisStore, "append_audit", fail_audit)
            with _calls(command) as calls:
                response = await runtime.bus.dispatch(command)
        after = snapshot(runtime.ledger.engine)
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path)
    try:
        restarted = snapshot(reopened.ledger.engine)
        evidence = {
            "kind": kind,
            "request": command.model_dump(mode="json"),
            "response": response.model_dump(mode="json"),
            "records": {k: v.model_dump(mode="json") for k, v in records.items()},
            "hits": hits,
            "calls": calls,
            "before": before,
            "after": after,
            "reopened": restarted,
            "expected_reads": reads,
        }
        _save("fault-" + kind + ".json", evidence)
        assert response.error is not None and len(hits) == 1 and hits[0]["transaction_active"]
        assert restarted == after
        for identifier in records:
            _assert_path(calls, identifier)
        rows = hits[0]["rows"]
        assert all(
            rows[k] is not None
            for k in (
                "hypothesis_records",
                "semantic_revisions",
                "working_heads",
                "hypothesis_audit",
                "entity_snapshots",
                "research_identities",
            )
        )
        assert rows["entity_snapshots"]["entity_id"] == hits[0]["entity"]
        assert rows["research_identities"]["snapshot_id"] == rows["entity_snapshots"]["snapshot_id"]
        assert len(rows["receipts"]) == 1
        if kind == "generate":
            assert len(records) == len(rows["batch_rows"]) == 3
            assert all(
                len(rows[name]) == 3
                for name in (
                    "batch_semantic_revisions",
                    "batch_working_heads",
                    "batch_research_identities",
                    "batch_receipts",
                )
            )
            assert (
                sum(r.evidence_basis == "SOURCE_GROUNDED_GENERATION" for r in records.values()) == 2
            )
        allowed = failed_command_allowances(reopened.ledger.engine, command, expected_reads=reads)
        assert_phase_delta(before, restarted, allowed)
        read_store = SqliteHypothesisStore(reopened.ledger.engine, reopened.ledger)
        if current is None:
            assert all(
                read_store.read_hypothesis(project, identifier, None) is None
                for identifier in records
            )
        else:
            await _read(reopened, project, current, "unchanged")
        recovered = value(
            await reopened.bus.dispatch(request(method, "73-recover-" + kind, payload))
        )
        recovered_records = (
            recovered["hypotheses"] if kind == "generate" else [recovered["hypothesis"]]
        )
        commits = recovered["commits"] if kind == "generate" else [recovered["commit"]]
        for index, item in enumerate(recovered_records):
            assert item["hypothesis_revision_id"] in commits[index]["receipt"]["subject_refs"]
            await _read(reopened, project, item, f"recovered-{index}")
        if kind == "intent":
            assert recovered["hypothesis"]["primary_intent"] == "PREDICTIVE"
            assert recovered["hypothesis"]["causal_profile"]["applicability"] == "NOT_APPLICABLE"
        if kind == "causal":
            assert recovered["hypothesis"]["causal_profile"]["mechanism"] == "clock-source mismatch"
        evidence.update(recovered=recovered, verdict="PASS")
        _save("fault-" + kind + ".json", evidence)
    finally:
        reopened.close()
