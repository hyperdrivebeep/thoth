"""Normal-entry prediction/measurement lineage, separate from process-success labels."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar, cast

import pytest
from pydantic import BaseModel
from tests.integration import test_a04_r2_closed_loop as a04_fixture
from tests.integration.test_a02_autonomous_acquisition import A02Connector, request
from tests.integration.test_a02_autonomous_acquisition import value as validated_value
from tests.integration.test_a04_r2_closed_loop import A04R2Model, prepare_a04

from thoth.adapters.sandbox import ScriptedSandboxAdapter
from thoth.adapters.storage.action import SqliteActionStore
from thoth.apps.runtime import AppRuntime, create_runtime
from thoth.domain.canonical import canonical_payload, domain_digest, model_digest
from thoth.domain.connectors import (
    ConnectorAccessRequest,
    ConnectorArtifactRef,
    ConnectorCheckpoint,
)
from thoth.domain.enums import ModelRole
from thoth.domain.model import ModelRequest, ModelResult
from thoth.domain.sandbox import SandboxExecutionState, SandboxResult, SandboxRunSpec
from thoth.protocol.jsonrpc import JsonRpcResponse

TModel = TypeVar("TModel", bound=BaseModel)


def value(response: JsonRpcResponse) -> dict[str, Any]:
    return cast(dict[str, Any], validated_value(response))


class MeasurementSandbox(ScriptedSandboxAdapter):
    """Fixture execution producer; raw samples are evaluated independently by product code."""

    def __init__(
        self,
        *,
        corrupt_unit: bool = False,
        report_updates: dict[str, object] | None = None,
        states: tuple[SandboxExecutionState, ...] = (),
    ) -> None:
        super().__init__(states)
        self.contract_digest = ""
        self.corrupt_unit = corrupt_unit
        self.report_updates = report_updates or {}
        self.on_result: Callable[[SandboxRunSpec], Awaitable[None]] | None = None
        self.model_assessment_refs: list[tuple[str, ...]] = []

    async def run(self, spec: SandboxRunSpec) -> SandboxResult:
        result = await super().run(spec)
        callback, self.on_result = self.on_result, None
        if callback is not None:
            await callback(spec)
        payload: dict[str, object] = {
            "schema_version": "1.0.0",
            "record_type": "RESEARCH_MEASUREMENT_OBSERVATION",
            "contract_digest": self.contract_digest,
            "procedure_version": "numeric-mean:1",
            "measure": "latency",
            "unit": "s" if self.corrupt_unit else "ms",
            "conditions": {"dataset_version": "trial-v1"},
            "samples": ["10", "12"],
            "input_digests": [item.content_sha256 for item in spec.input_snapshots],
            "observed_at": result.completed_at.isoformat(),
        }
        payload.update(self.report_updates)
        stdout = json.dumps(payload, sort_keys=True)
        return result.model_copy(
            update={
                "stdout": stdout,
                "output_digests": (hashlib.sha256(stdout.encode()).hexdigest(),),
                "runtime_version": "scripted:1",
                "security_tier": "TEST_ONLY",
            }
        )


async def prepare_measurement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    corrupt_unit: bool = False,
    report_updates: dict[str, object] | None = None,
    prespecification_state: str = "A_PRIORI",
    states: tuple[SandboxExecutionState, ...] = (),
    max_transient_retries: int = 0,
    choose_read_after_test: bool = False,
    assumption_candidates: tuple[str, ...] = (),
) -> tuple[AppRuntime, MeasurementSandbox, str, str, str]:
    sandbox = MeasurementSandbox(
        corrupt_unit=corrupt_unit, report_updates=report_updates, states=states
    )
    policy = a04_fixture.policy_payload

    def policy_with_retry(
        *, allow_sandbox: bool, include_template: bool = True
    ) -> dict[str, object]:
        return {
            **policy(allow_sandbox=allow_sandbox, include_template=include_template),
            "r2_recovery_policy": {"max_transient_retries": max_transient_retries},
        }

    monkeypatch.setattr(a04_fixture, "policy_payload", policy_with_retry)
    runtime, _adapter, project, thread = await prepare_a04(
        tmp_path, allow_sandbox=True, sandbox=sandbox
    )
    contract = {
        "schema_version": "1.0.0",
        "record_type": "RESEARCH_MEASUREMENT_CONTRACT",
        "contract_id": "measurement:latency-mean",
        "method": "ARITHMETIC_MEAN",
        "procedure_version": "numeric-mean:1",
        "measure": "latency",
        "unit": "ms",
        "conditions": {"dataset_version": "trial-v1"},
        "minimum_samples": 2,
        "maximum_samples": 100,
        "image_digest": "scripted:a04",
        "runtime_version": "scripted:1",
    }
    raw = json.dumps(contract, sort_keys=True).encode()
    sandbox.contract_digest = hashlib.sha256(raw).hexdigest()
    discover = A02Connector.discover

    async def discover_contract(
        self: A02Connector,
        access: ConnectorAccessRequest,
        checkpoint: ConnectorCheckpoint | None = None,
    ) -> tuple[ConnectorArtifactRef, ...]:
        is_contract = access.selector.get("relative_path") == "measurement.json"
        if is_contract:
            self.payloads["measurement.json"] = raw
        refs = await discover(self, access, checkpoint)
        return tuple(
            ref.model_copy(update={"media_type": "application/json"}) if is_contract else ref
            for ref in refs
        )

    monkeypatch.setattr(A02Connector, "discover", discover_contract)
    value(
        await runtime.bus.dispatch(
            request(
                "project/source/connect",
                "measurement-contract-source",
                {
                    "project_id": project,
                    "relative_path": "measurement.json",
                    "media_type": "application/json",
                    "authority": "INFORMAL",
                    "cutoff_state": "ELIGIBLE",
                    "security_class": "INTERNAL",
                },
            )
        )
    )
    spans = value(
        await runtime.bus.dispatch(
            request("evidence/list", "measurement-spans", {"project_id": project})
        )
    )["spans"]
    contract_span = next(
        item for item in spans if "RESEARCH_MEASUREMENT_CONTRACT" in item["exact_text"]
    )
    contract_ref = contract_span["artifact_id"]
    contract_refs = [item["span_id"] for item in spans if item["artifact_id"] == contract_ref]
    original = A04R2Model.structured

    async def proposals(self: A04R2Model, call: ModelRequest[TModel]) -> ModelResult[TModel]:
        result = await original(self, call)
        payload: dict[str, Any] = result.output.model_dump(mode="python")
        if call.role == ModelRole.HYPOTHESIS_GENERATOR:
            for ordinal, hypothesis in enumerate(payload["hypotheses"][:2]):
                hypothesis["primary_intent"] = "PREDICTIVE"
                hypothesis["assumptions"] = assumption_candidates
                hypothesis["statement"] = "The trial mean latency is within " + (
                    "9 to 13 ms" if ordinal == 0 else "20 to 30 ms"
                )
                hypothesis["prediction_proposal"] = {
                    "measurement_contract_ref": contract_ref,
                    "prespecification_state": prespecification_state,
                    "conditions": {"dataset_version": "trial-v1"},
                    "expected_outcome": {
                        "type": "RANGE",
                        "measure": "latency",
                        "unit": "ms",
                        "lower": "9" if ordinal == 0 else "20",
                        "upper": "13" if ordinal == 0 else "30",
                    },
                }
        elif call.role == ModelRole.ACTION_PLANNER:
            candidate = call.context_pack.candidate_portfolio
            assert candidate is not None
            selected_refs = {item.span_id for item in call.context_pack.evidence}
            selected_contract_refs = tuple(
                ref for ref in contract_refs if ref in selected_refs
            )
            assert selected_contract_refs, "measurement contract is absent from active evidence"
            payload["alternatives"][0]["primary_purpose"] = "HYPOTHESIS_DISCRIMINATION"
            payload["alternatives"][0]["hypothesis_ids"] = tuple(
                item.hypothesis_id for item in candidate.hypotheses[:2]
            )
            for action in payload["alternatives"]:
                action["source_refs"] = tuple(
                    dict.fromkeys((*action["source_refs"], *selected_contract_refs))
                )
            assessments = getattr(call.context_pack, "canonical_test_assessments", ())
            sandbox.model_assessment_refs.append(tuple(item.assessment_id for item in assessments))
            if choose_read_after_test and assessments:
                payload["proposed_frontier"] = (payload["alternatives"][1]["action_id"],)
        typed = call.output_model.model_validate(payload)
        return ModelResult(
            output=typed,
            model_id=result.model_id,
            prompt_version=result.prompt_version,
            scripted=True,
            input_digest=result.input_digest,
            output_digest=model_digest("N03_MODEL_OUTPUT", typed, schema_version="1.0.0"),
        )

    monkeypatch.setattr(A04R2Model, "structured", proposals)
    return runtime, sandbox, project, thread, str(contract_ref)


@pytest.mark.asyncio
async def test_next_normal_input_receives_qualified_tests_and_changes_next_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, sandbox, project, thread, _contract = await prepare_measurement(
        tmp_path, monkeypatch, choose_read_after_test=True
    )
    try:
        first = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "first-measurement",
                    {"project_id": project, "thread_id": thread},
                )
            )
        )
        expected = {
            item["assessment_id"]
            for item in first["r2_closed_loop"]["test_lifecycle"]["assessments"]
        }
        second = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "use-measurement",
                    {
                        "project_id": project,
                        "thread_id": thread,
                        "instruction": "Use the measured results to choose the next action.",
                    },
                )
            )
        )
        assert set(sandbox.model_assessment_refs[-1]) == expected
        assert len(sandbox.seen_specs) == 1
        assert second["action_plan"]["frontier"] == ["action:a04:read"]
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_normal_input_seals_predictions_and_derives_fit_from_actual_measurement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, sandbox, project, thread, _contract = await prepare_measurement(tmp_path, monkeypatch)
    try:
        analyzed = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input", "measured-cycle", {"project_id": project, "thread_id": thread}
                )
            )
        )
        assert "test_lifecycle" in analyzed["r2_closed_loop"], analyzed["r2_closed_loop"]
        lifecycle = analyzed["r2_closed_loop"]["test_lifecycle"]
        assert len(sandbox.seen_specs) == 1
        assert len(lifecycle["predictions"]) == 2
        assert {item["test_validity"] for item in lifecycle["assessments"]} == {"VALID"}
        assert {item["prediction_fit"] for item in lifecycle["assessments"]} == {
            "MATCH",
            "MISMATCH",
        }
        assert lifecycle["official_criterion_disposition"] == "NOT_ASSESSED"
        assert lifecycle["semantic_truth_certified"] is False
        execution_audits = [
            item
            for item in SqliteActionStore(runtime.ledger.engine).list_audit(project, None)
            if item.event_type == "action/researchExecutionBound"
        ]
        assert len(execution_audits) == 1
        audit = execution_audits[0]
        assert audit.event_digest == domain_digest(
            "ACTION_AUDIT",
            "1.0.0",
            canonical_payload(
                audit.model_dump(exclude={"audit_id", "schema_version", "event_digest"})
            ),
        )
        execution = value(
            await runtime.bus.dispatch(
                request(
                    "execution/read",
                    "measured-execution",
                    {
                        "project_id": project,
                        "plan_execution_id": lifecycle["execution_ref"],
                    },
                )
            )
        )["execution"]
        assert sandbox.seen_specs[0].attempt_id in execution["attempt_refs"]
        for prediction in lifecycle["predictions"]:
            assert datetime.fromisoformat(prediction["created_at"]) <= datetime.fromisoformat(
                analyzed["r2_closed_loop"]["sandbox_result"]["started_at"]
            )
            hypothesis = value(
                await runtime.bus.dispatch(
                    request(
                        "hypothesis/read",
                        prediction["prediction_id"],
                        {
                            "project_id": project,
                            "hypothesis_id": prediction["hypothesis_id"],
                        },
                    )
                )
            )["hypothesis"]
            assert prediction["prediction_id"] in hypothesis["prediction_refs"]
            assert hypothesis["empirical_appraisal"] in {"EVIDENCE_FAVORS", "EVIDENCE_AGAINST"}
    finally:
        runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "updates,expected,reason",
    [
        ({"unit": "s"}, "INVALID", "MEASUREMENT_UNIT_OR_MEASURE_MISMATCH"),
        ({"procedure_version": "unbound:2"}, "INVALID", "MEASUREMENT_VERSION_MISMATCH"),
        ({"samples": []}, "NOT_ASSESSABLE", "MEASUREMENT_OBSERVATION_SCHEMA_INVALID"),
        (
            {"observed_at": "2026-01-01T00:00:00Z"},
            "INVALID",
            "MEASUREMENT_PRESPECIFICATION_TIME_INVALID",
        ),
        ({"samples": ["NaN"]}, "NOT_ASSESSABLE", "MEASUREMENT_OBSERVATION_SCHEMA_INVALID"),
    ],
)
async def test_invalid_measurement_is_retained_without_substantive_appraisal_after_reopen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    updates: dict[str, object],
    expected: str,
    reason: str,
) -> None:
    runtime, sandbox, project, thread, _contract = await prepare_measurement(
        tmp_path, monkeypatch, report_updates=updates
    )
    try:
        analyzed = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input", "invalid-trial", {"project_id": project, "thread_id": thread}
                )
            )
        )
        lifecycle = analyzed["r2_closed_loop"]["test_lifecycle"]
        assert len(sandbox.seen_specs) == 1
        assert analyzed["r2_closed_loop"]["sandbox_result"]["exit_code"] == 0
        assert {item["test_validity"] for item in lifecycle["assessments"]} == {expected}
        assert all(reason in item["reasons"] for item in lifecycle["assessments"])
        assert all(item["substantive_update_allowed"] is False for item in lifecycle["assessments"])
        assert all(item["substantive_update_applied"] is False for item in lifecycle["appraisals"])
        predictions = lifecycle["predictions"]
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path / "allowed")
    try:
        for prediction in predictions:
            hypothesis = value(
                await reopened.bus.dispatch(
                    request(
                        "hypothesis/read",
                        prediction["prediction_id"],
                        {
                            "project_id": project,
                            "hypothesis_id": prediction["hypothesis_id"],
                        },
                    )
                )
            )["hypothesis"]
            assert hypothesis["empirical_appraisal"] == "UNASSESSED"
            assert hypothesis["test_refs"]
        for assessment in lifecycle["assessments"]:
            digest = reopened.ledger.read_heads(project)[
                f"HYPOTHESIS:{assessment['assessment_id']}"
            ]
            assert digest == assessment["assessment_digest"]
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_post_hoc_fit_is_recorded_without_confirmatory_appraisal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, _sandbox, project, thread, _contract = await prepare_measurement(
        tmp_path, monkeypatch, prespecification_state="POST_HOC"
    )
    try:
        analyzed = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input", "post-hoc-trial", {"project_id": project, "thread_id": thread}
                )
            )
        )
        lifecycle = analyzed["r2_closed_loop"]["test_lifecycle"]
        assert {item["test_validity"] for item in lifecycle["assessments"]} == {"VALID"}
        assert {item["prediction_fit"] for item in lifecycle["assessments"]} == {
            "MATCH",
            "MISMATCH",
        }
        assert all(
            "NONCONFIRMATORY_PRESPECIFICATION" in item["reasons"]
            for item in lifecycle["assessments"]
        )
        assert all(item["substantive_update_applied"] is False for item in lifecycle["appraisals"])
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_unbound_assumptions_limit_fit_without_empirical_support(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, _sandbox, project, thread, _contract = await prepare_measurement(
        tmp_path, monkeypatch, assumption_candidates=("The instrument calibration is valid",)
    )
    try:
        result = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "unbound-assumptions",
                    {"project_id": project, "thread_id": thread},
                )
            )
        )
        lifecycle = result["r2_closed_loop"]["test_lifecycle"]
        assert {item["test_validity"] for item in lifecycle["assessments"]} == {"LIMITED"}
        assert all(
            "ASSUMPTION_VALIDATION_PRODUCER_REQUIRED" in item["reasons"]
            for item in lifecycle["assessments"]
        )
        assert all(item["substantive_update_applied"] is False for item in lifecycle["appraisals"])
        assert all(
            item["execution_security_tier"] == "TEST_ONLY" for item in lifecycle["assessments"]
        )
    finally:
        runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cutoff,reason",
    [
        ("2026-09-01T00:00:00Z", None),
        ("2026-08-30T00:00:00Z", "PREDICTION_CUTOFF_BASIS_UNRESOLVED"),
        ("2100-01-01T00:00:00Z", "PREDICTION_CUTOFF_INVALID"),
    ],
)
async def test_public_validity_label_cannot_bind_a_nonexistent_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cutoff: str, reason: str | None
) -> None:
    runtime, _sandbox, project, thread, contract = await prepare_measurement(tmp_path, monkeypatch)
    try:
        work = value(
            await runtime.bus.dispatch(
                request(
                    "thread/read",
                    "read-work",
                    {
                        "project_id": project,
                        "thread_id": thread,
                    },
                )
            )
        )
        spans = value(
            await runtime.bus.dispatch(
                request("evidence/list", "all-evidence", {"project_id": project})
            )
        )["spans"]
        references = [item["span_id"] for item in spans]
        hypothesis = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/create",
                    "public-hypothesis",
                    {
                        "project_id": project,
                        "object_id": work["current_object_ids"][0],
                        "portfolio_id": "portfolio:a02",
                        "statement": "The controlled trial mean is within the predicted range",
                        "primary_intent": "PREDICTIVE",
                        "evidence_basis": "SOURCE",
                        "scope": {"dataset_version": "trial-v1"},
                        "evidence_refs": references,
                    },
                )
            )
        )["hypothesis"]
        before_prediction = dict(runtime.ledger.read_heads(project))
        prediction_response = await runtime.bus.dispatch(
            request(
                "hypothesis/prediction/bind",
                "prediction",
                {
                    "project_id": project,
                    "hypothesis_id": hypothesis["hypothesis_id"],
                    "hypothesis_revision_digest": hypothesis["revision_digest"],
                    "knowledge_cutoff": cutoff,
                    "prespecification_state": "A_PRIORI",
                    "conditions": {"dataset_version": "trial-v1"},
                    "measurement_contract_ref": contract,
                    "assumption_refs": [],
                    "expected_outcome": {
                        "type": "RANGE",
                        "measure": "latency",
                        "unit": "ms",
                        "lower": 9,
                        "upper": 13,
                    },
                    "discrimination_map": {},
                },
            )
        )
        if reason is not None:
            assert prediction_response.error is not None
            assert reason in prediction_response.error.message
            assert dict(runtime.ledger.read_heads(project)) == before_prediction
            return
        prediction = value(prediction_response)["prediction"]
        heads = dict(runtime.ledger.read_heads(project))
        response = await runtime.bus.dispatch(
            request(
                "hypothesis/test/bind",
                "forged-validity",
                {
                    "project_id": project,
                    "prediction_id": prediction["prediction_id"],
                    "execution_ref": "execution:does-not-exist",
                    "observation_refs": references,
                    "test_validity_assessment_ref": "assessment:does-not-exist",
                    "test_validity": "VALID",
                    "prediction_fit": "MATCH",
                },
            )
        )
        assert response.error is not None
        assert "TEST_VALIDITY_PRODUCER_NOT_FOUND" in response.error.message
        assert dict(runtime.ledger.read_heads(project)) == heads
        future = await runtime.bus.dispatch(
            request(
                "hypothesis/prediction/bind",
                "future-cutoff",
                {
                    "project_id": project,
                    "hypothesis_id": hypothesis["hypothesis_id"],
                    "hypothesis_revision_digest": hypothesis["revision_digest"],
                    "knowledge_cutoff": "2100-01-01T00:00:00Z",
                    "prespecification_state": "A_PRIORI",
                    "conditions": {"dataset_version": "trial-v1"},
                    "measurement_contract_ref": contract,
                    "assumption_refs": [],
                    "expected_outcome": {
                        "type": "RANGE",
                        "measure": "latency",
                        "unit": "ms",
                        "lower": 9,
                        "upper": 13,
                    },
                    "discrimination_map": {},
                },
            )
        )
        assert future.error is not None and "PREDICTION_CUTOFF_INVALID" in future.error.message
        assert dict(runtime.ledger.read_heads(project)) == heads
    finally:
        runtime.close()
