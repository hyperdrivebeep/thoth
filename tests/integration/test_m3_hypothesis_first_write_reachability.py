"""Observe genuine native first binding and rollback without fabricating unbound records."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from time import perf_counter
from typing import Any, cast

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
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
from thoth.adapters.storage.schema import hypothesis_test_bindings
from thoth.adapters.storage.test_validity_schema import research_test_assessments
from thoth.adapters.storage.transaction import read_connection
from thoth.application.services.hypothesis_service import HypothesisService
from thoth.application.services.hypothesis_test_lifecycle import HypothesisTestLifecycle
from thoth.application.services.r2_test_lifecycle import R2TestLifecycle
from thoth.application.services.sandbox_service import SandboxService
from thoth.application.services.test_validity_service import TestValidityService as ValidityService
from thoth.apps.runtime import create_runtime
from thoth.domain.resource_scope import current_resource_uses
from thoth.ports.model import ModelPort
from thoth.protocol.deferred import current_operation

OUT = Path(__file__).resolve().parents[2] / "outputs/refactor-step87-20261014"


def save(case: str, evidence: dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / (case + ".json")).write_text(
        json.dumps(evidence, indent=2, default=str) + "\n", encoding="utf-8"
    )


def rows_for(connection: Any, project: str, assessment: Any) -> dict[str, Any]:
    assessments = list(
        connection.execute(
            select(research_test_assessments).where(
                research_test_assessments.c.project_id == project,
                research_test_assessments.c.assessment_id == assessment.assessment_id,
            )
        ).mappings()
    )
    bindings = list(
        connection.execute(
            select(hypothesis_test_bindings).where(
                hypothesis_test_bindings.c.project_id == project,
                hypothesis_test_bindings.c.prediction_id == assessment.prediction_id,
            )
        ).mappings()
    )
    matching = [
        dict(row)
        for row in bindings
        if json.loads(row["content_json"])["test_validity_assessment_ref"]
        == assessment.assessment_id
    ]
    return {"assessments": [dict(row) for row in assessments], "bindings": matching}


def binding_request(project: str, assessment: dict[str, Any]) -> Any:
    return request(
        "hypothesis/test/bind",
        "87-public-bind",
        {
            "project_id": project,
            "prediction_id": assessment["prediction_id"],
            "execution_ref": assessment["execution_ref"],
            "observation_refs": assessment["observation_refs"],
            "test_validity_assessment_ref": assessment["assessment_id"],
            "test_validity": assessment["test_validity"],
            "prediction_fit": assessment["prediction_fit"],
        },
    )


@pytest.mark.parametrize("case", ["normal", "persist_failure"])
async def test_native_first_binding_visibility_and_post_persist_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    runtime, sandbox, project, thread, _contract = await prepare_measurement(tmp_path, monkeypatch)
    key = "87-" + case
    command = request("thread/input", key, {"project_id": project, "thread_id": thread})
    evidence: dict[str, Any] = {
        "case": case,
        "windows": [],
        "binding_writes": [],
        "trace": [],
        "observer_errors": [],
    }
    bound_call: ContextVar[bool] = ContextVar("step87_in_original_bind", default=False)
    injected = RuntimeError("87_FIRST_ASSESSMENT_PERSISTED_BEFORE_BINDING")
    originals: dict[str, Any] = {
        "accept": SandboxService.accepting_observation,
        "complete": R2TestLifecycle.complete,
        "bind": HypothesisService.bind_test,
        "validate": HypothesisTestLifecycle.validate_binding,
        "persist": ValidityService.persist,
        "write": SqliteHypothesisStore.add_test_binding,
        "reject": SandboxService.reject_observation,
    }
    external = runtime.ledger.engine.connect()
    dbapi = external.connection.driver_connection
    assert isinstance(dbapi, sqlite3.Connection)
    reused_command: Any = None
    reused: dict[str, Any] | None = None
    evidence["sqlite_settings_before"] = {
        "journal_mode": external.exec_driver_sql("PRAGMA journal_mode").scalar(),
        "busy_timeout": external.exec_driver_sql("PRAGMA busy_timeout").scalar(),
        "isolation_level": dbapi.isolation_level,
    }
    external.rollback()

    def active() -> bool:
        op = current_operation.get()
        return op is not None and (op.project_id, op.method, op.idempotency_key) == (
            project,
            "thread/input",
            key,
        )

    def observe_window(assessment: Any, validation: str) -> None:
        before_uses = current_resource_uses()
        window: dict[str, Any] = {
            "assessment": assessment.model_dump(mode="json"),
            "validation_completed": validation,
        }
        with read_connection(runtime.ledger.engine) as internal:
            distinct = (
                internal.connection.driver_connection is not external.connection.driver_connection
            )
            window.update(
                distinct_dbapi_connections=distinct, writer_in_transaction=internal.in_transaction()
            )
            if not distinct:
                evidence["observer_errors"].append("writer and observer share a DBAPI connection")
                save(case, evidence)
                raise RuntimeError("HOLD_CONTRACT_MISMATCH: observer connection is not independent")
            window["inside"] = rows_for(internal, project, assessment)
            started = perf_counter()
            try:
                window["outside"] = rows_for(external, project, assessment)
            except OperationalError as exc:
                window["external_error"] = {
                    "type": type(exc.orig).__name__,
                    "message": str(exc.orig),
                    "sqlite_errorname": getattr(exc.orig, "sqlite_errorname", None),
                }
                evidence["observer_errors"].append(window["external_error"])
                evidence["windows"].append(window)
                save(case, evidence)
                raise RuntimeError(
                    "HOLD_CONTRACT_MISMATCH: SQLite observer read failed; settings unchanged"
                ) from exc
            finally:
                window["external_read_seconds"] = perf_counter() - started
                # Close only the independent SELECT snapshot; writer is untouched.
                external.rollback()
            window["resource_uses_unchanged"] = before_uses == current_resource_uses()
        evidence["windows"].append(window)
        save(case, evidence)

    @contextmanager
    def accept(
        service: Any, spec: Any, bundle: Any, *, defer_rejection: bool = False
    ) -> Generator[Any]:
        if not active():
            with originals["accept"](
                service, spec, bundle, defer_rejection=defer_rejection
            ) as artifact:
                yield artifact
            return
        evidence["ambient_at_entry"] = vars(transaction_module)["_AMBIENT"].get() is not None
        evidence["attempt_id"] = spec.attempt_id
        evidence["artifact_id"] = bundle.observation.document.artifact.artifact_id
        evidence["observation_refs"] = [s.span_id for s in bundle.observation.evidence_candidates]
        evidence["before_phase"] = snapshot(runtime.ledger.engine)
        evidence["trace"].append("before_original_accept")
        try:
            with originals["accept"](
                service, spec, bundle, defer_rejection=defer_rejection
            ) as artifact:
                evidence["trace"].append("original_accept_entered")
                yield artifact
        except BaseException as exc:
            evidence["same_injected_exception"] = exc is injected
            evidence["trace"].append("original_accept_rolled_back")
            evidence["after_phase"] = snapshot(runtime.ledger.engine)
            save(case, evidence)
            raise
        else:
            evidence["trace"].append("original_accept_committed")
            evidence["after_phase"] = snapshot(runtime.ledger.engine)

    def complete(service: Any, prepared: Any, bundle: Any, assessments: Any) -> Any:
        if active():
            evidence["assessments"] = [a.model_dump(mode="json") for a in assessments]
            evidence["predictions"] = [p.model_dump(mode="json") for p in prepared.predictions]
            evidence["first_assessment_id"] = assessments[0].assessment_id
        return originals["complete"](service, prepared, bundle, assessments)

    def bind(service: Any, prediction: Any, **kwargs: Any) -> Any:
        if not active():
            return originals["bind"](service, prediction, **kwargs)
        token = bound_call.set(True)
        try:
            return originals["bind"](service, prediction, **kwargs)
        finally:
            bound_call.reset(token)

    def validate(service: Any, prediction: Any, **kwargs: Any) -> Any:
        assessment = originals["validate"](service, prediction, **kwargs)
        if case == "normal" and active() and bound_call.get():
            observe_window(assessment, "original validate_binding returned successfully")
        return assessment

    def persist(service: Any, assessment: Any) -> None:
        originals["persist"](service, assessment)
        if (
            case == "persist_failure"
            and active()
            and assessment.assessment_id == evidence.get("first_assessment_id")
        ):
            observe_window(
                assessment,
                "original persist and require_assessment returned; validate_binding not reached",
            )
            evidence["trace"].append("post_persist_fault")
            raise injected

    def write(store: Any, record: Any) -> None:
        originals["write"](store, record)
        op = current_operation.get()
        if (
            op is not None
            and op.project_id == project
            and op.idempotency_key in {key, "87-public-bind"}
        ):
            evidence["binding_writes"].append(
                {"key": op.idempotency_key, "record": record.model_dump(mode="json")}
            )

    def reject(service: Any, bundle: Any, *, reason: str) -> None:
        if active():
            evidence["trace"].append("reject_observation")
        originals["reject"](service, bundle, reason=reason)

    try:
        with monkeypatch.context() as patch:
            for owner, name, replacement in (
                (SandboxService, "accepting_observation", accept),
                (R2TestLifecycle, "complete", complete),
                (HypothesisService, "bind_test", bind),
                (HypothesisTestLifecycle, "validate_binding", validate),
                (ValidityService, "persist", persist),
                (SqliteHypothesisStore, "add_test_binding", write),
                (SandboxService, "reject_observation", reject),
            ):
                patch.setattr(owner, name, replacement)
            response = await runtime.bus.dispatch(command)
            evidence["response"] = response.model_dump(mode="json")
            evidence["terminal"] = snapshot(runtime.ledger.engine)
            save(case, evidence)
            assert not evidence["observer_errors"], (
                "HOLD_CONTRACT_MISMATCH: independent SQLite observation failed"
            )
            assert evidence["ambient_at_entry"] is False
            expected_windows = 2 if case == "normal" else 1
            assert len(evidence["windows"]) == expected_windows
            for window in evidence["windows"]:
                assert window["distinct_dbapi_connections"] and window["writer_in_transaction"]
                assert window["resource_uses_unchanged"]
                assert (
                    len(window["inside"]["assessments"]) == 1 and window["inside"]["bindings"] == []
                )
                assert window["outside"]["assessments"] == window["outside"]["bindings"] == [], (
                    "REACHABLE_OR_PARTIAL_PUBLICATION: stop and report before any concurrency test"
                )
            result = value(response)
            assert len(sandbox.seen_specs) == 1
            if case == "normal":
                lifecycle = result["r2_closed_loop"]["test_lifecycle"]
                assert len(lifecycle["assessments"]) == len(evidence["binding_writes"]) == 2
                assert all(a["substantive_update_applied"] for a in lifecycle["appraisals"])
                committed_pairs: list[dict[str, Any]] = []
                evidence["committed_pairs"] = committed_pairs
                with runtime.ledger.engine.connect() as connection:
                    for window in evidence["windows"]:
                        assessment = SimpleAssessment(window["assessment"])
                        rows = rows_for(connection, project, assessment)
                        assert len(rows["assessments"]) == len(rows["bindings"]) == 1
                        committed_pairs.append(rows)
                reused_command = binding_request(project, lifecycle["assessments"][0])
                reused = value(await runtime.bus.dispatch(reused_command))
                assert (
                    reused["test_binding"]["test_binding_id"]
                    == evidence["binding_writes"][0]["record"]["test_binding_id"]
                )
                assert len(evidence["binding_writes"]) == 2
                before_replay = snapshot(runtime.ledger.engine)
                assert value(await runtime.bus.dispatch(reused_command)) == reused
                assert snapshot(runtime.ledger.engine) == before_replay
            else:
                assert evidence["same_injected_exception"] and evidence["binding_writes"] == []
                assert evidence["before_phase"] == evidence["after_phase"], (
                    "HOLD_DEFECT: post-persist rollback retained data"
                )
                assert (
                    result["r2_closed_loop"]["reason_code"] == "RESEARCH_TEST_FINALIZATION_FAILED"
                )
                assert result["r2_closed_loop"]["terminal_state"] == "HOLD"
                assert (
                    evidence["trace"].index("post_persist_fault")
                    < evidence["trace"].index("original_accept_rolled_back")
                    < evidence["trace"].index("reject_observation")
                )
                attempt = SqliteExecutionStore(runtime.ledger.engine).read_attempt(
                    project, evidence["attempt_id"]
                )
                assert (
                    attempt is not None
                    and attempt.state == "SUCCEEDED"
                    and attempt.observation_completeness == "NOT_ADMITTED"
                )
                artifact = next(
                    json.loads(row)
                    for row in evidence["terminal"]["artifacts"]
                    if json.loads(row)["artifact_id"] == evidence["artifact_id"]
                )
                assert artifact["cutoff_state"] == "PROHIBITED_CONTEXT"
                with runtime.ledger.engine.connect() as connection:
                    for record in evidence["assessments"]:
                        rows = rows_for(connection, project, SimpleAssessment(record))
                        assert rows["assessments"] == rows["bindings"] == []
                evidence["terminal_attempt"] = attempt.model_dump(mode="json")
        evidence["sqlite_settings_after"] = {
            "journal_mode": external.exec_driver_sql("PRAGMA journal_mode").scalar(),
            "busy_timeout": external.exec_driver_sql("PRAGMA busy_timeout").scalar(),
            "isolation_level": dbapi.isolation_level,
        }
        external.rollback()
        assert evidence["sqlite_settings_before"] == evidence["sqlite_settings_after"]
        persisted = snapshot(runtime.ledger.engine)
        save(case, evidence)
    finally:
        external.close()
        runtime.close()
    reopened = create_runtime(
        tmp_path / "allowed",
        sandbox_adapter=sandbox,
        model_resolver=StaticModelResolver(cast(ModelPort, A04R2Model())),
    )
    try:
        assert snapshot(reopened.ledger.engine) == persisted
        assert value(await reopened.bus.dispatch(command)) == result
        assert snapshot(reopened.ledger.engine) == persisted
        assert len(sandbox.seen_specs) == 1
        if case == "normal":
            assert reused_command is not None and reused is not None
            assert value(await reopened.bus.dispatch(reused_command)) == reused
            assert snapshot(reopened.ledger.engine) == persisted
        else:
            again = value(
                await reopened.bus.dispatch(
                    request(
                        "thread/input", key + "-new", {"project_id": project, "thread_id": thread}
                    )
                )
            )
            assert (
                again["r2_closed_loop"].get("reason_code")
                == "RESEARCH_TEST_RESULT_RECONCILIATION_REQUIRED"
            )
            assert len(sandbox.seen_specs) == 1
            evidence["next_input"] = again
        evidence.update(
            verdict="PASS",
            reopened_exact=True,
            replay_exact=True,
            sandbox_execution_count=len(sandbox.seen_specs),
        )
        save(case, evidence)
    finally:
        reopened.close()


class SimpleAssessment:
    """Identifiers for diagnostic SELECT only; never passed to a product validator/writer."""

    def __init__(self, record: dict[str, Any]) -> None:
        self.assessment_id = str(record["assessment_id"])
        self.prediction_id = str(record["prediction_id"])
