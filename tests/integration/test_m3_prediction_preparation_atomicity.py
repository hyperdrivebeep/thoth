"""Observe the outer prediction preparation UoW; keep later failure records separate."""

from __future__ import annotations

import json
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import select
from tests.atomicity.harness import snapshot
from tests.integration.test_a02_autonomous_acquisition import StaticModelResolver
from tests.integration.test_a04_r2_closed_loop import A04R2Model
from tests.integration.test_hypothesis_prediction_outcome_cycle import (
    MeasurementSandbox,
    prepare_measurement,
    request,
    value,
)

from thoth.adapters.storage import transaction as transaction_module
from thoth.adapters.storage.action import SqliteActionStore
from thoth.adapters.storage.execution import SqliteExecutionStore
from thoth.adapters.storage.hypothesis import SqliteHypothesisStore
from thoth.adapters.storage.schema import metadata
from thoth.adapters.storage.sqlite import SqliteLedger, SqliteLedgerTransaction
from thoth.adapters.storage.transaction import read_connection
from thoth.application.services.hypothesis_test_lifecycle import HypothesisTestLifecycle
from thoth.application.services.r2_test_lifecycle import R2TestLifecycle
from thoth.apps.runtime import create_runtime
from thoth.domain.canonical import head_set_digest
from thoth.ports.model import ModelPort
from thoth.protocol.deferred import current_operation

_OUT = Path(__file__).resolve().parents[2] / "outputs/refactor-step71-20261014"
_POINTS = {
    "prediction_receipt": (SqliteLedgerTransaction, "insert_receipt", "receipts", "receipt_id"),
    "prediction": (
        SqliteHypothesisStore,
        "add_prediction",
        "hypothesis_predictions",
        "prediction_id",
    ),
    "prediction_audit": (SqliteHypothesisStore, "append_audit", "hypothesis_audit", "audit_id"),
    "plan": (SqliteActionStore, "add_plan", "action_plans", "plan_revision_id"),
    "attempt": (
        SqliteExecutionStore,
        "add_attempt",
        "step_execution_attempts",
        "attempt_revision_id",
    ),
    "attempt_audit": (SqliteExecutionStore, "append_audit", "execution_audit", "audit_id"),
}


def _save(point: str, evidence: dict[str, Any]) -> None:
    _OUT.mkdir(parents=True, exist_ok=True)
    (_OUT / f"phase-{point}.json").write_text(
        json.dumps(evidence, indent=2, default=str) + "\n", encoding="utf-8"
    )


@pytest.mark.parametrize("point", ["normal", *_POINTS])
async def test_preparation_rollback_prevents_measurement_and_allows_a_new_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, point: str
) -> None:
    runtime, sandbox, project, thread, _contract = await prepare_measurement(tmp_path, monkeypatch)
    key = "m3-preparation-" + point
    command = request("thread/input", key, {"project_id": project, "thread_id": thread})
    injected = RuntimeError("M3_PREPARATION_POST_WRITE:" + point)
    evidence: dict[str, Any] = {"point": point, "trace": [], "writes": [], "runs": []}
    state: dict[str, Any] = {
        "preparing": False,
        "inside": False,
        "selected": 0,
        "prepare_calls": 0,
        "basis_ids": [],
        "predictions": {},
        "seal_revision_ids": set(),
    }
    original_prepare = R2TestLifecycle.prepare
    original_basis = HypothesisTestLifecycle.prepare_prediction
    original_seal = HypothesisTestLifecycle.seal_prediction
    original_transaction = SqliteLedger.transaction
    original_revision = SqliteLedgerTransaction.insert_revision
    original_execution = SqliteExecutionStore.add_execution
    original_run = MeasurementSandbox.run

    def active() -> bool:
        operation = current_operation.get()
        return operation is not None and (
            operation.method,
            operation.idempotency_key,
            operation.project_id,
        ) == ("thread/input", key, project)

    def prepare(
        service: R2TestLifecycle, project_value: Any, portfolio: Any, action: Any, spec: Any
    ) -> Any:
        if not active():
            return original_prepare(service, project_value, portfolio, action, spec)
        state.update(
            preparing=True,
            wanted=set(action.hypothesis_ids),
            plan_id=spec.action_plan_id,
            action_id=action.action_id,
        )
        state["prepare_calls"] += 1
        evidence["action_hypothesis_ids"] = list(action.hypothesis_ids)
        try:
            prepared = original_prepare(service, project_value, portfolio, action, spec)
            if prepared is not None:
                evidence["prepared"] = {
                    "predictions": [p.model_dump(mode="json") for p in prepared.predictions],
                    "plan": prepared.plan.model_dump(mode="json"),
                    "execution": prepared.execution.model_dump(mode="json"),
                    "attempt": prepared.attempt.model_dump(mode="json"),
                    "spec_attempt_id": prepared.spec.attempt_id,
                }
            return prepared
        finally:
            evidence["trace"].append("prepare-return-or-error")
            state["preparing"] = False

    def basis(service: HypothesisTestLifecycle, current: Any, **kwargs: Any) -> Any:
        prepared = original_basis(service, current, **kwargs)
        if active() and state["preparing"] and current.hypothesis_id in state["wanted"]:
            state["basis_ids"].append(current.hypothesis_id)
            evidence["basis_ids"] = list(state["basis_ids"])
        return prepared

    def seal(
        service: HypothesisTestLifecycle, current: Any, prediction: Any, prepared_basis: Any
    ) -> None:
        if active() and state["inside"] and current.hypothesis_id in state["wanted"]:
            state["predictions"][current.hypothesis_id] = prediction.prediction_id
            evidence["prediction_lineage"] = dict(state["predictions"])
        original_seal(service, current, prediction, prepared_basis)

    @contextmanager
    def transaction(ledger: SqliteLedger) -> Generator[Any]:
        chosen = (
            active()
            and state["preparing"]
            and state["selected"] == 0
            and len(state["basis_ids"]) == 2
            and set(state["basis_ids"]) == state["wanted"]
            and ledger.engine is runtime.ledger.engine
        )
        if not chosen:
            with original_transaction(ledger) as tx:
                yield tx
            return
        state["selected"] += 1
        evidence["ambient_at_entry"] = vars(transaction_module)["_AMBIENT"].get() is not None
        evidence["trace"].append("outer-before-enter")
        if not evidence["ambient_at_entry"]:
            evidence["before"] = snapshot(runtime.ledger.engine)
        try:
            with original_transaction(ledger) as tx:
                state["inside"] = True
                evidence["trace"].append("outer-entered")
                try:
                    yield tx
                finally:
                    state["inside"] = False
        except BaseException as exc:
            evidence["trace"].append("outer-exception-exit")
            evidence["same_exception"] = exc is injected
            evidence["exception_type"] = type(exc).__name__
            evidence["after_phase"] = snapshot(runtime.ledger.engine)
            raise
        else:
            evidence["trace"].append("outer-committed")
            evidence["after_phase"] = snapshot(runtime.ledger.engine)

    def revision(transaction_value: SqliteLedgerTransaction, record: Any) -> None:
        original_revision(transaction_value, record)
        if active() and state["inside"]:
            second = state["predictions"].get(state["basis_ids"][1])
            if record.entity_id == second and record.reason == "seal bound research test evidence":
                state["seal_revision_ids"].add(record.revision_id)

    def execution(store: SqliteExecutionStore, record: Any) -> None:
        original_execution(store, record)
        if (
            active()
            and state["inside"]
            and record.plan_id == state["plan_id"]
            and record.plan_revision_digest == state.get("plan_digest")
        ):
            state["execution_id"] = record.plan_execution_id
            evidence["execution_lineage"] = record.model_dump(mode="json")

    async def run(instance: MeasurementSandbox, spec: Any) -> Any:
        if active() and instance is sandbox:
            evidence["trace"].append("measurement-run")
            evidence["runs"].append(
                {
                    "ambient": vars(transaction_module)["_AMBIENT"].get() is not None,
                    "preparing": state["preparing"],
                    "inside": state["inside"],
                    "attempt_id": spec.attempt_id,
                }
            )
        return await original_run(instance, spec)

    def wrap_write(name: str, original: Any) -> Any:
        def write(instance: Any, record: Any) -> Any:
            result = original(instance, record)
            if not active() or not state["inside"] or record.project_id != project:
                return result
            first_hypothesis, second_hypothesis = state["basis_ids"]
            first_prediction = state["predictions"].get(first_hypothesis)
            second_prediction = state["predictions"].get(second_hypothesis)
            matched = False
            if name == "prediction_receipt":
                matched = bool(state["seal_revision_ids"].intersection(record.subject_refs))
            elif name == "prediction":
                matched = (
                    record.prediction_id == second_prediction
                    and record.hypothesis_id == second_hypothesis
                )
            elif name == "prediction_audit":
                matched = (
                    record.hypothesis_id == second_hypothesis
                    and record.event_type == "hypothesis/predictionBound"
                )
            elif name == "plan":
                matched = (
                    record.plan_id == state["plan_id"]
                    and record.validation_state == "EXECUTION_BOUND"
                )
                if matched:
                    steps = [s for s in record.steps if s.get("action_id") == state["action_id"]]
                    evidence["bound_steps"] = steps
                    state["selected_steps"] = {s["step_id"] for s in steps}
                    state["plan_digest"] = record.revision_digest
            elif name == "attempt":
                matched = (
                    record.plan_execution_id == state.get("execution_id")
                    and record.plan_revision_digest == state.get("plan_digest")
                    and record.step_id in state.get("selected_steps", set())
                )
                if matched:
                    state["attempt_id"] = record.attempt_id
            elif name == "attempt_audit":
                matched = (
                    record.plan_execution_id == state.get("execution_id")
                    and record.event_type == "execution/attemptStarted"
                    and record.payload.get("attempt_id") == state.get("attempt_id")
                )
            if not matched:
                return result
            _, _method, table_name, field = _POINTS[name]
            table = metadata.tables[table_name]
            with read_connection(runtime.ledger.engine) as connection:
                row = (
                    connection.execute(
                        select(table).where(
                            table.c.project_id == project, table.c[field] == getattr(record, field)
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                predictions = metadata.tables["hypothesis_predictions"]
                first = (
                    connection.execute(
                        select(predictions).where(
                            predictions.c.project_id == project,
                            predictions.c.prediction_id == first_prediction,
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                heads = metadata.tables["working_heads"]
                first_head = (
                    connection.execute(
                        select(heads).where(
                            heads.c.project_id == project,
                            heads.c.aggregate_key == "HYPOTHESIS:" + str(first_prediction),
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                first_record = metadata.tables["hypothesis_records"]
                first_revisions = (
                    connection.execute(
                        select(first_record).where(
                            first_record.c.project_id == project,
                            first_record.c.hypothesis_id == first_hypothesis,
                        )
                    )
                    .mappings()
                    .all()
                )
                first_bound = [
                    dict(r)
                    for r in first_revisions
                    if first_prediction in json.loads(r["content_json"])["prediction_refs"]
                ]
                ledger_connection = cast(dict[str, Any], vars(instance)).get("_connection")
                evidence["writes"].append(
                    {
                        "point": name,
                        "row": None if row is None else dict(row),
                        "first_prediction": None if first is None else dict(first),
                        "first_head": None if first_head is None else dict(first_head),
                        "first_bound": first_bound,
                        "inside": state["inside"],
                        "ambient": vars(transaction_module)["_AMBIENT"].get() is not None,
                        "same_connection": ledger_connection is None
                        or ledger_connection is connection,
                        "transaction_active": connection.in_transaction(),
                        "second_hypothesis": second_hypothesis,
                        "second_prediction": second_prediction,
                        "seal_revision_ids": sorted(state["seal_revision_ids"]),
                    }
                )
            if point == name:
                evidence["trace"].append("injected-runtime-error")
                raise injected
            return result

        return write

    try:
        with monkeypatch.context() as patch:
            patch.setattr(R2TestLifecycle, "prepare", prepare)
            patch.setattr(HypothesisTestLifecycle, "prepare_prediction", basis)
            patch.setattr(HypothesisTestLifecycle, "seal_prediction", seal)
            patch.setattr(SqliteLedger, "transaction", transaction)
            patch.setattr(SqliteLedgerTransaction, "insert_revision", revision)
            patch.setattr(SqliteExecutionStore, "add_execution", execution)
            patch.setattr(MeasurementSandbox, "run", run)
            for name, (owner, method, _table, _field) in _POINTS.items():
                patch.setattr(owner, method, wrap_write(name, getattr(owner, method)))
            response = await runtime.bus.dispatch(command)
        evidence.update(
            response=response.model_dump(mode="json"),
            selected=state["selected"],
            prepare_calls=state["prepare_calls"],
            terminal=snapshot(runtime.ledger.engine),
            sandbox_runs=len(sandbox.seen_specs),
        )
        _save(point, evidence)
        # Wrapper observations are judged here, outside product exception handlers.
        assert state["selected"] == state["prepare_calls"] == 1
        assert evidence["ambient_at_entry"] is False
        assert len(state["basis_ids"]) == len(set(state["basis_ids"])) == 2
        assert set(state["basis_ids"]) == state["wanted"]
        assert evidence["writes"]
        for observed in evidence["writes"]:
            assert (
                observed["row"] is not None
                and observed["first_prediction"] is not None
                and observed["first_head"] is not None
            )
            assert len(observed["first_bound"]) == 1
            assert (
                observed["inside"]
                and observed["ambient"]
                and observed["transaction_active"]
                and observed["same_connection"]
            )
        if point == "normal":
            assert {w["point"] for w in evidence["writes"]} == set(_POINTS)
            assert len(evidence["runs"]) == len(sandbox.seen_specs) == 1
            assert evidence["runs"][0] == {
                "ambient": False,
                "preparing": False,
                "inside": False,
                "attempt_id": state["attempt_id"],
            }
            assert evidence["trace"].index("outer-committed") < evidence["trace"].index(
                "measurement-run"
            )
            prepared = evidence["prepared"]
            assert (
                prepared["spec_attempt_id"]
                == prepared["attempt"]["attempt_id"]
                == state["attempt_id"]
            )
            assert prepared["execution"]["selected_step_ids"] == [prepared["attempt"]["step_id"]]
            lifecycle = value(response)["r2_closed_loop"]["test_lifecycle"]
            assert all(a["substantive_update_applied"] for a in lifecycle["appraisals"])
        else:
            assert response.error is not None
            assert len([w for w in evidence["writes"] if w["point"] == point]) == 1
            assert (
                evidence["same_exception"] is True and evidence["exception_type"] == "RuntimeError"
            )
            assert evidence["before"] == evidence["after_phase"]
            assert evidence["trace"].index("outer-exception-exit") < evidence["trace"].index(
                "prepare-return-or-error"
            )
            assert sandbox.seen_specs == [] and evidence["runs"] == []
            work = value(
                await runtime.bus.query(
                    request(
                        "thread/read", "after-failure", {"project_id": project, "thread_id": thread}
                    )
                )
            )
            evidence["thread_read"] = work
            assert work["working_head_digest"] == head_set_digest(
                runtime.ledger.read_heads(project)
            )
        _save(point, evidence)
    finally:
        runtime.close()
    reopened = create_runtime(
        tmp_path / "allowed",
        sandbox_adapter=sandbox,
        model_resolver=StaticModelResolver(cast(ModelPort, A04R2Model())),
    )
    try:
        evidence["reopened"] = snapshot(reopened.ledger.engine)
        _save(point, evidence)
        assert evidence["reopened"] == evidence["terminal"]
        replay = await reopened.bus.dispatch(command)
        evidence["replay"] = replay.model_dump(mode="json")
        if point == "normal":
            assert value(replay) == value(response)
            assert len(sandbox.seen_specs) == 1
        else:
            assert replay.error is not None and response.error is not None
            assert replay.error.model_dump(mode="json") == response.error.model_dump(mode="json")
            assert sandbox.seen_specs == []
            assert snapshot(reopened.ledger.engine) == evidence["terminal"]
            recovered = value(
                await reopened.bus.dispatch(
                    request(
                        "thread/input", key + "-retry", {"project_id": project, "thread_id": thread}
                    )
                )
            )
            evidence["recovered"] = recovered
            _save(point, evidence)
            assert len(sandbox.seen_specs) == 1
            lifecycle = recovered["r2_closed_loop"]["test_lifecycle"]
            assert len(lifecycle["predictions"]) == 2
            assert all(a["test_validity"] == "VALID" for a in lifecycle["assessments"])
            assert all(a["substantive_update_applied"] for a in lifecycle["appraisals"])
        evidence["verdict"] = "PASS"
        _save(point, evidence)
    finally:
        reopened.close()
