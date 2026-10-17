"""Existing producer-owned bindings are reused; first-write reachability stays open."""

from __future__ import annotations

import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event
from typing import Any, cast

import pytest
from tests.atomicity.harness import (
    AllowedRow,
    assert_phase_delta,
    failed_command_allowances,
    snapshot,
)
from tests.integration.test_a02_autonomous_acquisition import StaticModelResolver
from tests.integration.test_a04_r2_closed_loop import A04R2Model
from tests.integration.test_hypothesis_prediction_outcome_cycle import (
    prepare_measurement,
    request,
    value,
)

from thoth.adapters.storage import transaction as transaction_module
from thoth.adapters.storage.hypothesis import SqliteHypothesisStore
from thoth.apps.runtime import create_runtime
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.event import Checkpoint, EventRecord
from thoth.domain.resource_scope import OperationResourceBinding
from thoth.ports.model import ModelPort
from thoth.protocol.deferred import current_operation
from thoth.protocol.jsonrpc import JsonRpcRequest, RpcErrorCode

_OUT = Path(__file__).resolve().parents[2] / "outputs/refactor-step72-20261014"
_METHOD = "hypothesis/test/bind"


def _save(case: str, evidence: dict[str, Any]) -> None:
    _OUT.mkdir(parents=True, exist_ok=True)
    round_id = os.environ.get("THOTH_STEP72_ROUND", "1")
    (_OUT / f"{case}-round{round_id}.json").write_text(
        json.dumps(evidence, indent=2, default=str) + "\n", encoding="utf-8"
    )


def _rows(state: dict[str, tuple[str, ...]], table: str) -> list[dict[str, Any]]:
    return [json.loads(r) for r in state[table]]


def _success_allowances(
    state: dict[str, tuple[str, ...]],
    command: JsonRpcRequest,
    expected_value: dict[str, Any],
    reads: tuple[str, ...],
) -> tuple[AllowedRow, ...]:
    project, key = command.params.input["project_id"], command.params.meta.idempotency_key
    selected = [
        r
        for r in _rows(state, "operations")
        if r["project_id"] == project and r["method"] == _METHOD and r["idempotency_key"] == key
    ]
    assert len(selected) == 1
    operation = selected[0]
    scope = domain_digest(
        "RPC_SCOPE",
        "1.0.0",
        canonical_payload(
            {
                "method": command.method,
                "input": command.params.input,
                "expected_head_digest": command.params.meta.expected_head_digest,
                "field_session_id": command.params.meta.field_session_id,
            }
        ),
    )
    assert operation["state"] == "SUCCEEDED" and operation["scope_digest"] == scope
    assert operation["error_json"] is None and operation["completed_at"] is not None
    assert json.loads(operation["result_json"]) == expected_value
    identifier = operation["operation_id"]
    key_rows = [r for r in _rows(state, "idempotency_keys") if r["operation_id"] == identifier]
    assert key_rows == [
        {
            "project_id": project,
            "method": _METHOD,
            "idempotency_key": key,
            "scope_digest": scope,
            "operation_id": identifier,
        }
    ]
    result_digest = domain_digest("OPERATION_RESULT", "1.0.0", canonical_payload(expected_value))
    events = [r for r in _rows(state, "events") if r["operation_id"] == identifier]
    names = ("operation.started", "hypothesis/testBound", "operation.succeeded")
    assert sorted(r["event_type"] for r in events) == sorted(names)
    previous = None
    for name in names:
        row = next(r for r in events if r["event_type"] == name)
        expected_payload = (
            {"method": _METHOD} if name == names[0] else {"result_digest": result_digest}
        )
        if name == "hypothesis/testBound":
            expected_payload["method"] = _METHOD
        assert row["project_id"] == project and json.loads(row["payload_json"]) == expected_payload
        assert row["previous_event_digest"] == previous
        event = EventRecord.model_validate(
            {**{k: v for k, v in row.items() if k != "payload_json"}, "payload": expected_payload}
        )
        assert row["event_digest"] == domain_digest(
            "OPERATION_EVENT",
            "1.0.0",
            canonical_payload(event.model_dump(mode="python", exclude={"event_digest"})),
        )
        previous = row["event_digest"]
    checkpoints = [r for r in _rows(state, "checkpoints") if r["operation_id"] == identifier]
    assert len(checkpoints) == 1
    checkpoint = checkpoints[0]
    payload = {"state": "SUCCEEDED", "result_digest": result_digest}
    assert json.loads(checkpoint["payload_json"]) == payload
    record = Checkpoint.model_validate(
        {**{k: v for k, v in checkpoint.items() if k != "payload_json"}, "payload": payload}
    )
    assert checkpoint["checkpoint_digest"] == domain_digest(
        "OPERATION_CHECKPOINT",
        "1.0.0",
        canonical_payload(record.model_dump(mode="python", exclude={"checkpoint_digest"})),
    )
    bindings = [
        r for r in _rows(state, "operation_resource_bindings") if r["operation_id"] == identifier
    ]
    assert len(bindings) == 1
    binding = OperationResourceBinding.model_validate_json(bindings[0]["content_json"])
    assert binding.project_id == project and binding.request_digest == scope
    assert binding.output_kind == "RESULT" and binding.output_digest == domain_digest(
        "OPERATION_RESOURCE_OUTPUT", "1.0.0", canonical_payload(expected_value)
    )
    assert bindings[0]["binding_digest"] == binding.binding_digest
    assert sorted((r.resource_ref, r.capability) for r in binding.resource_uses) == [
        (r, "READ") for r in reads
    ]
    allowed = [AllowedRow("operations", operation, "added", {})]
    for table, rows in (
        ("idempotency_keys", key_rows),
        ("events", events),
        ("checkpoints", checkpoints),
        ("operation_resource_bindings", bindings),
    ):
        allowed.extend(AllowedRow(table, r, "added", {}) for r in rows)
    return tuple(allowed)


@pytest.mark.parametrize("case", ["single", "read_failure", "different_keys", "same_key"])
async def test_existing_test_binding_is_not_written_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    runtime, sandbox, project, thread, _contract = await prepare_measurement(tmp_path, monkeypatch)
    second_runtime = None
    evidence: dict[str, Any] = {
        "case": case,
        "lookups": [],
        "add_calls": [],
        "initial_responses": [],
    }
    try:
        produced = value(
            await runtime.bus.dispatch(
                request("thread/input", "produce", {"project_id": project, "thread_id": thread})
            )
        )
        lifecycle = produced["r2_closed_loop"]["test_lifecycle"]
        assessment = lifecycle["assessments"][0]
        prediction = next(
            r for r in lifecycle["predictions"] if r["prediction_id"] == assessment["prediction_id"]
        )
        store = SqliteHypothesisStore(runtime.ledger.engine, runtime.ledger)
        existing = store.list_test_bindings(project, prediction["prediction_id"])
        assert len(existing) == 1
        binding = existing[0].model_dump(mode="json")
        current = store.read_hypothesis(project, prediction["hypothesis_id"], None)
        assert current is not None
        body: dict[str, object] = {
            "project_id": project,
            "prediction_id": prediction["prediction_id"],
            "execution_ref": assessment["execution_ref"],
            "observation_refs": assessment["observation_refs"],
            "test_validity_assessment_ref": assessment["assessment_id"],
            "test_validity": assessment["test_validity"],
            "prediction_fit": assessment["prediction_fit"],
        }
        expected = {
            "test_binding": binding,
            "appraisal_update_allowed": existing[0].appraisal_mutation_allowed,
            "affected_portfolio": None,
        }
        command = request(_METHOD, "72-" + case, body)
        observation_artifacts = {
            r["artifact_id"]
            for r in _rows(snapshot(runtime.ledger.engine), "evidence_spans")
            if r["span_id"] in assessment["observation_refs"]
        }
        reads = tuple(
            sorted(
                {
                    f"revision:{prediction['hypothesis_revision_digest']}",
                    f"revision:{current.revision_digest}",
                    f"revision:{assessment['plan_revision_digest']}",
                    prediction["measurement_contract_ref"],
                    *assessment["observation_refs"],
                    *observation_artifacts,
                }
            )
        )
        evidence.update(
            binding=binding, expected_reads=reads, request=command.model_dump(mode="json")
        )
        if case in {"different_keys", "same_key"}:
            second_runtime = create_runtime(
                tmp_path / "allowed",
                sandbox_adapter=sandbox,
                model_resolver=StaticModelResolver(cast(ModelPort, A04R2Model())),
            )
        before = snapshot(runtime.ledger.engine)
        evidence["before"] = before
        original_list = SqliteHypothesisStore.list_test_bindings
        original_add = SqliteHypothesisStore.add_test_binding
        rendezvous = Barrier(2, timeout=10)
        owner_entered, peer_returned = Event(), Event()
        keys = {command.params.meta.idempotency_key, "72-different-peer"}

        def scoped() -> bool:
            operation = current_operation.get()
            return (
                operation is not None
                and operation.method == _METHOD
                and operation.project_id == project
                and operation.idempotency_key in keys
            )

        def add(instance: SqliteHypothesisStore, record: Any) -> None:
            original_add(instance, record)
            if scoped():
                evidence["add_calls"].append(record.model_dump(mode="json"))

        def lookup(instance: SqliteHypothesisStore, p: str, identifier: str | None) -> Any:
            records = original_list(instance, p, identifier)
            if not scoped() or p != project or identifier != prediction["prediction_id"]:
                return records
            ambient = vars(transaction_module)["_AMBIENT"].get() is not None
            evidence["lookups"].append(
                {"ambient": ambient, "ids": [r.test_binding_id for r in records]}
            )
            if ambient:
                raise RuntimeError("BIND_OBSERVATION_HAS_AMBIENT_TRANSACTION")
            if case == "read_failure":
                raise RuntimeError("M3_EXISTING_BINDING_READ_FAILURE")
            if case == "different_keys":
                rendezvous.wait()
            if case == "same_key":
                owner_entered.set()
                if not peer_returned.wait(timeout=10):
                    raise RuntimeError("BIND_PEER_EVENT_TIMEOUT")
            return records

        successful_commands: list[JsonRpcRequest] = []
        with monkeypatch.context() as patch:
            patch.setattr(SqliteHypothesisStore, "list_test_bindings", lookup)
            patch.setattr(SqliteHypothesisStore, "add_test_binding", add)
            if case in {"single", "read_failure"}:
                response = await runtime.bus.dispatch(command)
                evidence["initial_responses"].append(response.model_dump(mode="json"))
                after = snapshot(runtime.ledger.engine)
                evidence["after"] = after
                _save(case, evidence)
                if case == "single":
                    assert value(response) == expected
                    successful_commands.append(command)
                    assert value(await runtime.bus.dispatch(command)) == expected
                    conflict = await runtime.bus.dispatch(
                        request(
                            _METHOD,
                            command.params.meta.idempotency_key,
                            {
                                **body,
                                "prediction_fit": "MISMATCH"
                                if assessment["prediction_fit"] == "MATCH"
                                else "MATCH",
                            },
                        )
                    )
                    assert (
                        conflict.error is not None
                        and conflict.error.code == RpcErrorCode.IDEMPOTENCY_CONFLICT
                    )
                    assert snapshot(runtime.ledger.engine) == after
                else:
                    assert response.error is not None and len(evidence["lookups"]) == 1
            else:
                assert second_runtime is not None
                peer_command = (
                    command if case == "same_key" else request(_METHOD, "72-different-peer", body)
                )

                def owner_worker() -> Any:
                    if case == "same_key":
                        rendezvous.wait()
                    return asyncio.run(runtime.bus.dispatch(command))

                def peer_worker() -> Any:
                    assert second_runtime is not None
                    if case == "same_key":
                        rendezvous.wait()
                        if not owner_entered.wait(timeout=10):
                            raise RuntimeError("BIND_OWNER_EVENT_TIMEOUT")
                    try:
                        return asyncio.run(second_runtime.bus.dispatch(peer_command))
                    finally:
                        peer_returned.set()

                with ThreadPoolExecutor(max_workers=2) as pool:
                    owner_future, peer_future = pool.submit(owner_worker), pool.submit(peer_worker)
                    responses = [owner_future.result(timeout=30), peer_future.result(timeout=30)]
                evidence["initial_responses"] = [r.model_dump(mode="json") for r in responses]
                _save(case, evidence)
                assert value(responses[0]) == expected
                if case == "same_key":
                    peer = responses[1]
                    assert peer.error is None and peer.result is not None
                    assert peer.result["state"] == "RUNNING" and peer.result["value"] is None
                    assert peer.result["execution_observation"] == "UNKNOWN"
                    assert len(evidence["lookups"]) == 1
                    successful_commands = [command]
                else:
                    assert value(responses[1]) == expected and len(evidence["lookups"]) == 2
                    successful_commands = [command, peer_command]
        # Workers and all bounded synchronization have finished before final replay.
        for completed in successful_commands:
            assert value(await runtime.bus.dispatch(completed)) == expected
        after = snapshot(runtime.ledger.engine)
        evidence["after"] = after
        _save(case, evidence)
        assert evidence["add_calls"] == [] and len(sandbox.seen_specs) == 1
        if case == "read_failure":
            allowed = failed_command_allowances(
                runtime.ledger.engine, command, expected_reads=reads
            )
        else:
            allowed = tuple(
                row
                for completed in successful_commands
                for row in _success_allowances(after, completed, expected, reads)
            )
        assert_phase_delta(before, after, allowed)
        rows = [
            json.loads(r["content_json"])
            for r in _rows(after, "hypothesis_test_bindings")
            if r["prediction_id"] == prediction["prediction_id"]
        ]
        assert rows == [binding]
        evidence["terminal"] = after
        _save(case, evidence)
    finally:
        runtime.close()
        if second_runtime is not None:
            second_runtime.close()
    reopened = create_runtime(
        tmp_path / "allowed",
        sandbox_adapter=sandbox,
        model_resolver=StaticModelResolver(cast(ModelPort, A04R2Model())),
    )
    try:
        evidence["reopened"] = snapshot(reopened.ledger.engine)
        _save(case, evidence)
        assert evidence["reopened"] == evidence["terminal"]
        if case == "read_failure":
            retried = request(_METHOD, "72-retry-read", body)
            assert value(await reopened.bus.dispatch(retried)) == expected
            after_retry = snapshot(reopened.ledger.engine)
            assert_phase_delta(
                after, after_retry, _success_allowances(after_retry, retried, expected, reads)
            )
        readback = value(
            await reopened.bus.dispatch(
                request(
                    "hypothesis/prediction/read",
                    "72-readback",
                    {"project_id": project, "prediction_id": prediction["prediction_id"]},
                )
            )
        )
        evidence["readback"] = readback
        assert readback["test_bindings"] == [binding]
        assert len(sandbox.seen_specs) == 1
        evidence["signature"] = {
            "initial_states": [
                r["result"]["state"] if r["result"] is not None else "ERROR"
                for r in evidence["initial_responses"]
            ],
            "lookup_count": len(evidence["lookups"]),
            "new_binding_writes": 0,
            "binding_count": 1,
            "extra_measurements": 0,
            "operation_count": len(successful_commands) if successful_commands else 1,
        }
        evidence["verdict"] = "PASS"
        _save(case, evidence)
    finally:
        reopened.close()
