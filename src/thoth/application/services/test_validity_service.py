"""Prepare a measurement assessment outside the write transaction, then seal it atomically."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import cast

from pydantic import ValidationError

from thoth.application.services.hypothesis_test_lifecycle import (
    HypothesisTestLifecycle,
    seal_research_test_record,
)
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.execution_full import PlanExecutionRecord, StepExecutionAttemptRecord
from thoth.domain.hypothesis_full import HypothesisRecord, PredictionRecord
from thoth.domain.sandbox import (
    SandboxExecutionState,
    SandboxReceipt,
    SandboxResult,
    SandboxRunSpec,
)
from thoth.domain.test_validity import (
    ExpectedRange,
    PredictionFit,
    ResearchMeasurementObservation,
    TestValidity,
    TestValidityAssessment,
)
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.ledger import LedgerPort
from thoth.ports.object_store import ObjectStorePort
from thoth.ports.runtime import ClockPort, IdGeneratorPort
from thoth.ports.test_validity import ResearchTestEvaluatorPort, TestValidityStorePort


@dataclass(frozen=True)
class ResearchTestRun:
    execution: PlanExecutionRecord
    attempt: StepExecutionAttemptRecord
    spec: SandboxRunSpec
    result: SandboxResult
    receipt: SandboxReceipt
    observation_refs: tuple[str, ...]


class TestValidityService:
    def __init__(
        self,
        *,
        store: TestValidityStorePort,
        lifecycle: HypothesisTestLifecycle,
        evaluator: ResearchTestEvaluatorPort,
        artifacts: ArtifactLedgerPort,
        objects: ObjectStorePort,
        ledger: LedgerPort,
        clock: ClockPort,
        ids: IdGeneratorPort,
    ) -> None:
        self._store = store
        self._lifecycle = lifecycle
        self._evaluator = evaluator
        self._artifacts = artifacts
        self._objects = objects
        self._ledger = ledger
        self._clock = clock
        self._ids = ids

    def prepare(
        self, prediction: PredictionRecord, current: HypothesisRecord, run: ResearchTestRun
    ) -> TestValidityAssessment:
        self._lifecycle.require_prediction(prediction, current)
        self._check_run(prediction, run)
        raw_digest = self._read_capture(run)
        output_digest = hashlib.sha256(run.result.stdout.encode()).hexdigest()
        existing = self._store.find_attempt(
            prediction.project_id, prediction.prediction_id, run.attempt.attempt_id
        )
        if existing is not None:
            if (
                existing.prediction_digest != prediction.prediction_digest
                or existing.raw_observation_digest != raw_digest
                or existing.measurement_output_digest != output_digest
            ):
                raise ValueError("TEST_RESULT_IDEMPOTENCY_CONFLICT")
            return existing
        contract = prediction.measurement_contract
        if (
            contract is None
            or prediction.measurement_contract_digest is None
            or prediction.hypothesis_semantic_digest is None
        ):
            raise ValueError("PREDICTION_MEASUREMENT_NOT_SEALED")
        reasons: list[str] = []
        observation: ResearchMeasurementObservation | None = None
        measured: Decimal | None = None
        validity: TestValidity = "NOT_ASSESSABLE"
        fit: PredictionFit = "NOT_OBSERVED"
        if len(run.result.stdout.encode()) > 65_536:
            reasons.append("MEASUREMENT_OUTPUT_TOO_LARGE")
        else:
            try:
                observation = ResearchMeasurementObservation.model_validate_json(run.result.stdout)
            except ValidationError:
                reasons.append("MEASUREMENT_OBSERVATION_SCHEMA_INVALID")
        if observation is not None:
            reasons.extend(self._measurement_mismatches(prediction, run, observation))
            validity = "INVALID" if reasons else "VALID"
            if not reasons:
                try:
                    measured = self._evaluator.evaluate(contract, observation)
                    expected = ExpectedRange.model_validate(prediction.expected_outcome)
                    fit = "MATCH" if expected.lower <= measured <= expected.upper else "MISMATCH"
                except (ValueError, ArithmeticError):
                    validity = "NOT_ASSESSABLE"
                    fit = "INCONCLUSIVE"
                    measured = None
                    reasons.append("MEASUREMENT_PRODUCER_OR_EXPECTATION_UNAVAILABLE")
        confirmatory = prediction.prespecification_state == "A_PRIORI"
        if prediction.unbound_assumption_candidates or prediction.assumption_refs:
            reasons.append("ASSUMPTION_VALIDATION_PRODUCER_REQUIRED")
            if validity == "VALID":
                validity = "LIMITED"
            confirmatory = False
        if prediction.prespecification_state != "A_PRIORI":
            reasons.append("NONCONFIRMATORY_PRESPECIFICATION")
        draft: dict[str, object] = {
            "assessment_id": self._ids.new("test-validity"),
            "project_id": prediction.project_id,
            "object_id": current.object_id,
            "hypothesis_id": prediction.hypothesis_id,
            "hypothesis_revision_digest": prediction.hypothesis_revision_digest,
            "hypothesis_semantic_digest": prediction.hypothesis_semantic_digest,
            "prediction_id": prediction.prediction_id,
            "prediction_digest": prediction.prediction_digest,
            "execution_ref": run.execution.plan_execution_id,
            "attempt_ref": run.attempt.attempt_id,
            "plan_id": run.execution.plan_id,
            "plan_revision_digest": run.execution.plan_revision_digest,
            "observation_refs": run.observation_refs,
            "measurement_contract_ref": prediction.measurement_contract_ref,
            "measurement_contract_digest": prediction.measurement_contract_digest,
            "sandbox_receipt_digest": run.receipt.receipt_digest,
            "input_context_digest": run.spec.input_context_digest,
            "policy_digest": run.spec.policy_digest,
            "knowledge_cutoff": prediction.knowledge_cutoff,
            "scope": contract.conditions,
            "test_validity": validity,
            "prediction_fit": fit,
            "measured_value": measured,
            "reasons": tuple(reasons) or ("BOUND_MEASUREMENT_PROTOCOL_SATISFIED",),
            "producer_ref": f"{contract.method}@{contract.procedure_version}",
            "runtime_profile": run.receipt.runtime_profile.value,
            "execution_security_tier": run.receipt.security_tier,
            "raw_observation_digest": raw_digest,
            "measurement_output_digest": output_digest,
            "substantive_update_allowed": validity == "VALID" and confirmatory,
            "official_criterion_disposition": "NOT_ASSESSED",
            "semantic_truth_certified": False,
            "created_at": self._clock.now(),
            "schema_version": "1.0.0",
        }
        return TestValidityAssessment.model_validate(
            {
                **draft,
                "assessment_digest": domain_digest(
                    "TEST_VALIDITY_ASSESSMENT", "1.0.0", canonical_payload(draft)
                ),
            }
        )

    def persist(self, assessment: TestValidityAssessment) -> None:
        """The caller includes execution completion, appraisal, and this seal in one UoW."""
        existing = self._store.find_attempt(
            assessment.project_id, assessment.prediction_id, assessment.attempt_ref
        )
        if existing is not None:
            if existing != assessment:
                raise ValueError("TEST_RESULT_IDEMPOTENCY_CONFLICT")
            self._lifecycle.require_assessment(existing.project_id, existing.assessment_id)
            return
        seal_research_test_record(
            self._ledger,
            self._clock,
            self._ids,
            assessment,
            evidence_refs=assessment.observation_refs,
        )
        self._store.add_assessment(assessment)
        self._lifecycle.require_assessment(assessment.project_id, assessment.assessment_id)

    @staticmethod
    def _check_run(prediction: PredictionRecord, run: ResearchTestRun) -> None:
        if (
            run.execution.project_id != prediction.project_id
            or run.execution.object_id != prediction.object_id
            or run.attempt.project_id != prediction.project_id
            or run.attempt.plan_execution_id != run.execution.plan_execution_id
            or run.attempt.plan_revision_digest != run.execution.plan_revision_digest
            or run.spec.attempt_id != run.attempt.attempt_id
            or run.result.attempt_id != run.attempt.attempt_id
            or run.receipt.attempt_id != run.attempt.attempt_id
            or run.spec.action_plan_id != run.execution.plan_id
            or run.spec.project_id != prediction.project_id
            or run.result.project_id != prediction.project_id
            or run.receipt.project_id != prediction.project_id
            or not run.spec.input_context_digest
        ):
            raise ValueError("RESEARCH_TEST_RUN_IDENTITY_MISMATCH")

    def _read_capture(self, run: ResearchTestRun) -> str:
        spans = tuple(
            self._artifacts.read_evidence(reference) for reference in run.observation_refs
        )
        if not spans or any(
            span is None or span.project_id != run.spec.project_id for span in spans
        ):
            raise ValueError("RESEARCH_TEST_OBSERVATION_MISSING")
        artifacts = {span.artifact_id for span in spans if span is not None}
        if len(artifacts) != 1:
            raise ValueError("RESEARCH_TEST_OBSERVATION_AMBIGUOUS")
        artifact = self._artifacts.read_artifact(next(iter(artifacts)))
        if artifact is None or artifact.source_uri != f"sandbox://{run.spec.attempt_id}/result":
            raise ValueError("RESEARCH_TEST_OBSERVATION_ORIGIN_MISMATCH")
        raw = self._objects.read(artifact.byte_sha256)
        if len(raw) > 2_097_152 or hashlib.sha256(raw).hexdigest() != artifact.byte_sha256:
            raise ValueError("RESEARCH_TEST_OBSERVATION_DIGEST_MISMATCH")
        captured = json.loads(raw)
        if not isinstance(captured, dict):
            raise ValueError("RESEARCH_TEST_CAPTURE_MISMATCH")
        payload = cast(dict[str, object], captured)
        if (
            payload.get("stdout") != run.result.stdout
            or payload.get("attempt_id") != run.spec.attempt_id
            or payload.get("sandbox_receipt_digest") != run.receipt.receipt_digest
        ):
            raise ValueError("RESEARCH_TEST_CAPTURE_MISMATCH")
        return artifact.byte_sha256

    @staticmethod
    def _measurement_mismatches(
        prediction: PredictionRecord,
        run: ResearchTestRun,
        observation: ResearchMeasurementObservation,
    ) -> tuple[str, ...]:
        contract = prediction.measurement_contract
        assert contract is not None
        checks = (
            (
                "MEASUREMENT_PROCESS_FAILED",
                run.result.state == SandboxExecutionState.SUCCEEDED and run.result.exit_code == 0,
            ),
            (
                "MEASUREMENT_CONTRACT_DIGEST_MISMATCH",
                observation.contract_digest == prediction.measurement_contract_digest,
            ),
            (
                "MEASUREMENT_VERSION_MISMATCH",
                observation.procedure_version == contract.procedure_version,
            ),
            (
                "MEASUREMENT_UNIT_OR_MEASURE_MISMATCH",
                (observation.measure, observation.unit) == (contract.measure, contract.unit),
            ),
            (
                "MEASUREMENT_CONDITIONS_MISMATCH",
                observation.conditions == contract.conditions == prediction.conditions,
            ),
            (
                "MEASUREMENT_SAMPLE_COUNT_INVALID",
                contract.minimum_samples <= len(observation.samples) <= contract.maximum_samples,
            ),
            (
                "MEASUREMENT_INPUT_BINDING_MISMATCH",
                sorted(observation.input_digests)
                == sorted(item.content_sha256 for item in run.spec.input_snapshots),
            ),
            ("MEASUREMENT_IMAGE_MISMATCH", run.spec.image_digest == contract.image_digest),
            (
                "MEASUREMENT_RUNTIME_VERSION_MISMATCH",
                run.receipt.runtime_version == contract.runtime_version,
            ),
            (
                "MEASUREMENT_PRESPECIFICATION_TIME_INVALID",
                prediction.created_at
                <= run.result.started_at
                <= observation.observed_at
                <= run.result.completed_at,
            ),
            (
                "MEASUREMENT_ASSUMPTION_UNVERIFIED",
                all(
                    observation.assumption_results.get(reference) == "SATISFIED"
                    for reference in prediction.assumption_refs
                ),
            ),
        )
        return tuple(reason for reason, valid in checks if not valid)
