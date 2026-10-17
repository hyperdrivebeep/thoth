"""Observe the post-I/O acceptance UoW without replacing its entry or exit."""

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
    prepare_measurement,
    request,
    value,
)

from thoth.adapters.storage import transaction as transaction_module
from thoth.adapters.storage.execution import SqliteExecutionStore
from thoth.adapters.storage.hypothesis import SqliteHypothesisStore
from thoth.adapters.storage.schema import metadata
from thoth.adapters.storage.sqlite import SqliteLedgerTransaction
from thoth.adapters.storage.test_validity import SqliteTestValidityStore
from thoth.adapters.storage.transaction import read_connection
from thoth.application.services.r2_test_lifecycle import R2TestLifecycle
from thoth.application.services.sandbox_service import SandboxService
from thoth.apps.runtime import create_runtime
from thoth.ports.model import ModelPort
from thoth.protocol.deferred import current_operation

_OUT = Path(__file__).resolve().parents[2] / "outputs/refactor-step70-20261014"
_POINTS = {
    "add_assessment": (SqliteTestValidityStore, "research_test_assessments", "assessment_id"),
    "add_test_binding": (SqliteHypothesisStore, "hypothesis_test_bindings", "test_binding_id"),
    "add_appraisal": (SqliteHypothesisStore, "hypothesis_appraisals", "appraisal_id"),
    "insert_revision": (SqliteLedgerTransaction, "semantic_revisions", "revision_id"),
    "insert_receipt": (SqliteLedgerTransaction, "receipts", "receipt_id"),
    "append_audit": (SqliteHypothesisStore, "hypothesis_audit", "audit_id"),
}


def _save(point: str, evidence: dict[str, Any]) -> None:
    _OUT.mkdir(parents=True, exist_ok=True)
    (_OUT / f"phase-{point}.json").write_text(
        json.dumps(evidence, indent=2, default=str) + "\n", encoding="utf-8"
    )


@pytest.mark.parametrize("point", ["normal", *_POINTS])
async def test_post_io_acceptance_is_atomic_and_does_not_repeat_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, point: str
) -> None:
    runtime, sandbox, project, thread, _contract = await prepare_measurement(tmp_path, monkeypatch)
    key = "m3-acceptance-" + point
    command = request("thread/input", key, {"project_id": project, "thread_id": thread})
    injected = RuntimeError("M3_SUBSTANTIVE_POST_WRITE:" + point)
    evidence: dict[str, Any] = {
        "point": point,
        "exception_type": "RuntimeError",
        "trace": [],
        "hits": [],
    }
    target: dict[str, Any] = {}
    revision_ids: set[str] = set()
    binding_ids: set[str] = set()
    phase_attempt: list[str] = []
    original_accept = SandboxService.accepting_observation
    original_complete = R2TestLifecycle.complete
    original_reject = SandboxService.reject_observation

    def active() -> bool:
        operation = current_operation.get()
        return operation is not None and (
            operation.method,
            operation.idempotency_key,
            operation.project_id,
        ) == ("thread/input", key, project)

    @contextmanager
    def observe_accept(service: SandboxService, spec: Any, bundle: Any) -> Generator[Any]:
        if not active() or spec.project_id != project:
            with original_accept(service, spec, bundle) as artifact:
                yield artifact
            return
        phase_attempt.append(spec.attempt_id)
        evidence["ambient_at_entry"] = vars(transaction_module)["_AMBIENT"].get() is not None
        evidence["trace"].append("accept-before-original-enter")
        evidence["bundle"] = {
            "attempt_id": spec.attempt_id,
            "artifact_id": bundle.observation.document.artifact.artifact_id,
            "observation_refs": [s.span_id for s in bundle.observation.evidence_candidates],
            "result": bundle.result.model_dump(mode="json"),
            "receipt": bundle.receipt.model_dump(mode="json"),
        }
        if not evidence["ambient_at_entry"]:
            evidence["before"] = snapshot(runtime.ledger.engine)
        try:
            with original_accept(service, spec, bundle) as artifact:
                evidence["trace"].append("original-entered")
                yield artifact
        except BaseException as exc:
            evidence["trace"].append("original-exited-with-exception")
            evidence["same_exception"] = exc is injected
            evidence["observed_exception"] = type(exc).__name__
            evidence["after_phase"] = snapshot(runtime.ledger.engine)
            raise
        else:
            evidence["trace"].append("original-committed")
            evidence["after_phase"] = snapshot(runtime.ledger.engine)

    def observe_complete(
        service: R2TestLifecycle, prepared: Any, bundle: Any, assessments: Any
    ) -> Any:
        if active() and prepared.attempt.attempt_id in phase_attempt:
            evidence["predictions"] = [p.model_dump(mode="json") for p in prepared.predictions]
            evidence["assessments"] = [a.model_dump(mode="json") for a in assessments]
            evidence["execution"] = prepared.execution.model_dump(mode="json")
            by_prediction = {a.prediction_id: a for a in assessments}
            if len(prepared.predictions) == 2:
                first, second = prepared.predictions
                target.update(
                    first_hypothesis=first.hypothesis_id,
                    first_assessment=by_prediction[first.prediction_id].assessment_id,
                    prediction=second.prediction_id,
                    hypothesis=second.hypothesis_id,
                    assessment=by_prediction[second.prediction_id].assessment_id,
                    attempt=prepared.attempt.attempt_id,
                )
            evidence["target"] = dict(target)
        return original_complete(service, prepared, bundle, assessments)

    def observe_reject(service: SandboxService, bundle: Any, *, reason: str) -> None:
        if active() and bundle.result.attempt_id in phase_attempt:
            evidence["trace"].append("reject-observation")
            evidence["reject_reason"] = reason
        original_reject(service, bundle, reason=reason)

    def wrap_write(name: str, original: Any) -> Any:
        def write(instance: Any, record: Any) -> Any:
            result = original(instance, record)
            if not active() or not target or record.project_id != project:
                return result
            matches = False
            if name == "add_assessment":
                matches = (record.assessment_id, record.prediction_id, record.attempt_ref) == (
                    target["assessment"],
                    target["prediction"],
                    target["attempt"],
                )
            elif name == "add_test_binding":
                matches = (
                    record.test_validity_assessment_ref == target["assessment"]
                    and record.prediction_id == target["prediction"]
                )
                if matches:
                    binding_ids.add(record.test_binding_id)
            elif name == "add_appraisal":
                matches = record.hypothesis_id == target["hypothesis"] and bool(
                    binding_ids.intersection(record.test_assessment_refs)
                )
                if matches:
                    evidence["second_substantive"] = record.substantive_update_applied
            elif name == "insert_revision":
                matches = (
                    record.entity_id == target["hypothesis"]
                    and record.reason == "hypothesis/appraisalUpdated"
                )
                if matches:
                    revision_ids.add(record.revision_id)
            elif name == "insert_receipt":
                matches = bool(revision_ids.intersection(record.subject_refs))
            elif name == "append_audit":
                matches = (
                    record.hypothesis_id == target["hypothesis"]
                    and record.event_type == "hypothesis/appraisalUpdated"
                )
            if not matches or name != point:
                return result
            _, table_name, field = _POINTS[name]
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
                first_table = metadata.tables["hypothesis_appraisals"]
                first_rows = (
                    connection.execute(
                        select(first_table).where(
                            first_table.c.project_id == project,
                            first_table.c.hypothesis_id == target["first_hypothesis"],
                        )
                    )
                    .mappings()
                    .all()
                )
                binding_table = metadata.tables["hypothesis_test_bindings"]
                all_bindings = (
                    connection.execute(
                        select(binding_table).where(binding_table.c.project_id == project)
                    )
                    .mappings()
                    .all()
                )
                first_bindings = [
                    dict(r)
                    for r in all_bindings
                    if json.loads(r["content_json"])["test_validity_assessment_ref"]
                    == target["first_assessment"]
                ]
                ledger_connection = cast(dict[str, Any], vars(instance)).get("_connection")
                evidence["hits"].append(
                    {
                        "point": name,
                        "row": None if row is None else dict(row),
                        "first_appraisals": [dict(r) for r in first_rows],
                        "first_bindings": first_bindings,
                        "transaction_active": connection.in_transaction(),
                        "ambient_owned": vars(transaction_module)["_AMBIENT"].get() is not None,
                        "same_ledger_connection": ledger_connection is None
                        or ledger_connection is connection,
                        "staged_revision_ids": sorted(revision_ids),
                    }
                )
            evidence["trace"].append("injected-runtime-error")
            raise injected

        return write

    try:
        with monkeypatch.context() as patch:
            patch.setattr(SandboxService, "accepting_observation", observe_accept)
            patch.setattr(SandboxService, "reject_observation", observe_reject)
            patch.setattr(R2TestLifecycle, "complete", observe_complete)
            for name, (owner, _table, _field) in _POINTS.items():
                patch.setattr(owner, name, wrap_write(name, getattr(owner, name)))
            response = await runtime.bus.dispatch(command)
        evidence["response"] = response.model_dump(mode="json")
        evidence["terminal"] = snapshot(runtime.ledger.engine)
        evidence["sandbox_runs"] = len(sandbox.seen_specs)
        _save(point, evidence)
        # All verdicts are outside wrappers so product catch-all cannot swallow them.
        assert phase_attempt and len(phase_attempt) == 1
        assert evidence["ambient_at_entry"] is False
        assert len(evidence.get("predictions", [])) == 2
        result = value(response)
        if point == "normal":
            lifecycle = result["r2_closed_loop"]["test_lifecycle"]
            assert {a["test_validity"] for a in lifecycle["assessments"]} == {"VALID"}
            assert {a["prediction_fit"] for a in lifecycle["assessments"]} == {"MATCH", "MISMATCH"}
            assert all(a["substantive_update_applied"] is True for a in lifecycle["appraisals"])
            assert lifecycle["semantic_truth_certified"] is False
            assert lifecycle["official_criterion_disposition"] == "NOT_ASSESSED"
            assert evidence["trace"][-1] == "original-committed"
        else:
            assert len(evidence["hits"]) == 1
            hit = evidence["hits"][0]
            assert (
                hit["row"] is not None
                and hit["transaction_active"]
                and hit["ambient_owned"]
                and hit["same_ledger_connection"]
            )
            assert len(hit["first_appraisals"]) == len(hit["first_bindings"]) == 1
            first = json.loads(hit["first_appraisals"][0]["content_json"])
            assert first["substantive_update_applied"] is True
            assert hit["first_bindings"][0]["test_binding_id"] in first["test_assessment_refs"]
            assert (
                evidence["same_exception"] is True
                and evidence["observed_exception"] == "RuntimeError"
            )
            assert evidence["before"] == evidence["after_phase"]
            trace = evidence["trace"]
            assert (
                trace.index("injected-runtime-error")
                < trace.index("original-exited-with-exception")
                < trace.index("reject-observation")
            )
            assert result["r2_closed_loop"]["reason_code"] == "RESEARCH_TEST_FINALIZATION_FAILED"
            assert result["r2_closed_loop"]["terminal_state"] == "HOLD"
        assert len(sandbox.seen_specs) == 1
        attempt = SqliteExecutionStore(runtime.ledger.engine).read_attempt(
            project, phase_attempt[0]
        )
        assert attempt is not None and attempt.state == "SUCCEEDED"
        evidence["attempt"] = attempt.model_dump(mode="json")
        if point != "normal":
            assert attempt.observation_completeness == "NOT_ADMITTED"
            terminal = evidence["terminal"]
            artifacts = [json.loads(r) for r in terminal["artifacts"]]
            artifact = next(
                r for r in artifacts if r["artifact_id"] == evidence["bundle"]["artifact_id"]
            )
            assert artifact["cutoff_state"] == "PROHIBITED_CONTEXT"
            spans = [json.loads(r) for r in terminal["evidence_spans"]]
            observed = [r for r in spans if r["span_id"] in evidence["bundle"]["observation_refs"]]
            assert len(observed) == len(evidence["bundle"]["observation_refs"])
            assert all(r["cutoff_state"] == "PROHIBITED_CONTEXT" for r in observed)
            controls = [
                json.loads(json.loads(r)["content_json"]) for r in terminal["control_records"]
            ]
            admission = [
                r
                for r in controls
                if r["namespace"] == "SANDBOX"
                and r["record_id"] == "sandbox-admission:" + phase_attempt[0]
            ]
            assert admission and all(r["state"] == "HELD" for r in admission)
            hypothesis_ids = {p["hypothesis_id"] for p in evidence["predictions"]}
            prediction_ids = {p["prediction_id"] for p in evidence["predictions"]}
            for table, identifier, ids in (
                ("hypothesis_records", "hypothesis_id", hypothesis_ids),
                ("hypothesis_appraisals", "hypothesis_id", hypothesis_ids),
                ("hypothesis_test_bindings", "prediction_id", prediction_ids),
                ("research_test_assessments", "prediction_id", prediction_ids),
            ):
                selected: list[list[str]] = []
                for state in (evidence["before"], terminal):
                    selected.append([r for r in state[table] if json.loads(r)[identifier] in ids])
                assert selected[0] == selected[1], table
        records = SqliteHypothesisStore(runtime.ledger.engine, runtime.ledger).list_hypotheses(
            project
        )
        current = {r.hypothesis_id: r for r in records}
        for prediction in evidence["predictions"]:
            hypothesis = current[prediction["hypothesis_id"]]
            if point == "normal":
                assert hypothesis.empirical_appraisal in {"EVIDENCE_FAVORS", "EVIDENCE_AGAINST"}
            else:
                assert hypothesis.empirical_appraisal == "UNASSESSED" and not hypothesis.test_refs
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
        assert value(replay) == result
        assert len(sandbox.seen_specs) == 1
        if point != "normal":
            again = value(
                await reopened.bus.dispatch(
                    request(
                        "thread/input", key + "-new", {"project_id": project, "thread_id": thread}
                    )
                )
            )
            evidence["next_input"] = again
            assert (
                again["r2_closed_loop"].get("reason_code")
                == "RESEARCH_TEST_RESULT_RECONCILIATION_REQUIRED"
            )
            assert len(sandbox.seen_specs) == 1
        evidence["verdict"] = "PASS"
        _save(point, evidence)
    finally:
        reopened.close()
