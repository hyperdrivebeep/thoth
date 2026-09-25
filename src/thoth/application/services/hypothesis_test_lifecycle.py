"""Bind public prediction/test/appraisal transitions to immutable producer evidence."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from thoth.application.services.revision_service import RevisionCommitService
from thoth.domain.actor import ActorRef
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import ActorKind, CutoffState, EntityType
from thoth.domain.execution_full import PlanExecutionRecord, StepExecutionAttemptRecord
from thoth.domain.hypothesis_full import HypothesisRecord, HypothesisTestBinding, PredictionRecord
from thoth.domain.revision import (
    EntitySnapshot,
    ImpactPropagationPlan,
    RevisionChangeSet,
    SemanticRevision,
    StagedRevision,
)
from thoth.domain.test_validity import (
    ExpectedRange,
    ResearchMeasurementContract,
    TestValidityAssessment,
    hypothesis_semantic_digest,
)
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.execution import ExecutionStorePort
from thoth.ports.hypothesis import HypothesisStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.object_store import ObjectStorePort
from thoth.ports.project import ProjectStorePort
from thoth.ports.runtime import ClockPort, IdGeneratorPort
from thoth.ports.test_validity import TestValidityStorePort


@dataclass(frozen=True)
class PreparedPredictionBasis:
    contract: ResearchMeasurementContract
    contract_digest: str
    contract_ref: str
    source_refs: tuple[str, ...]
    hypothesis_digest: str
    semantic_digest: str
    project_revision: int
    knowledge_cutoff: datetime
    expected_outcome: ExpectedRange


def seal_research_test_record(
    ledger: LedgerPort,
    clock: ClockPort,
    ids: IdGeneratorPort,
    record: PredictionRecord | TestValidityAssessment,
    *,
    evidence_refs: tuple[str, ...],
) -> None:
    identifier = (
        record.prediction_id if isinstance(record, PredictionRecord) else record.assessment_id
    )
    digest = (
        record.prediction_digest
        if isinstance(record, PredictionRecord)
        else record.assessment_digest
    )
    key = f"HYPOTHESIS:{identifier}"
    if key in ledger.read_heads(record.project_id):
        raise ValueError("RESEARCH_TEST_RECORD_ALREADY_SEALED")
    content = record.model_dump(mode="python")
    snapshot = EntitySnapshot(
        snapshot_id=ids.new("snapshot"),
        project_id=record.project_id,
        entity_type=EntityType.HYPOTHESIS,
        entity_id=identifier,
        schema_version="1.0.0",
        content=content,
        content_digest=domain_digest("ENTITY_SNAPSHOT", "1.0.0", canonical_payload(content)),
    )
    actor = ActorRef(
        actor_id="agent:research-test-lifecycle",
        kind=ActorKind.AGENT,
        role="research-test-producer",
    )
    revision = SemanticRevision(
        revision_id=ids.new("research-test-revision"),
        project_id=record.project_id,
        entity_type=EntityType.HYPOTHESIS,
        entity_id=identifier,
        snapshot_id=snapshot.snapshot_id,
        parent_revision_digests=(),
        actor=actor,
        reason="seal bound research test evidence",
        evidence_refs=evidence_refs,
        affected_refs=(),
        revision_digest=digest,
        created_at=record.created_at,
    )
    commit = RevisionCommitService(ledger, clock, ids, policy_version="research-test:1.0.0").commit(
        RevisionChangeSet(
            changeset_id=ids.new("changeset"),
            project_id=record.project_id,
            expected_heads={},
            staged_revisions=(StagedRevision(snapshot=snapshot, revision=revision),),
            impact_plan=ImpactPropagationPlan(),
            actor=actor,
            reason=revision.reason,
        )
    )
    if not commit.committed_revision_ids:
        raise ValueError("RESEARCH_TEST_SEAL_CONFLICT")


class HypothesisTestLifecycle:
    def __init__(
        self,
        *,
        hypotheses: HypothesisStorePort,
        assessments: TestValidityStorePort,
        executions: ExecutionStorePort,
        artifacts: ArtifactLedgerPort,
        objects: ObjectStorePort,
        projects: ProjectStorePort,
        ledger: LedgerPort,
        clock: ClockPort,
        ids: IdGeneratorPort,
    ) -> None:
        self._hypotheses = hypotheses
        self._assessments = assessments
        self._executions = executions
        self._artifacts = artifacts
        self._objects = objects
        self._projects = projects
        self._ledger = ledger
        self._clock = clock
        self._ids = ids

    def prepare_prediction(
        self,
        current: HypothesisRecord,
        *,
        knowledge_cutoff: datetime,
        measurement_contract_ref: str,
        conditions: dict[str, str],
        expected_outcome: dict[str, object],
    ) -> PreparedPredictionBasis:
        project = self._projects.read(current.project_id)
        if (
            project is None
            or knowledge_cutoff.tzinfo is None
            or knowledge_cutoff > project.cutoff_at
            or knowledge_cutoff > self._clock.now()
        ):
            raise ValueError("PREDICTION_CUTOFF_INVALID")
        # The source eligibility seal is for the Project cutoff. It contains no finer
        # temporal provenance that could establish eligibility at an earlier cutoff.
        if knowledge_cutoff != project.cutoff_at:
            raise ValueError("PREDICTION_CUTOFF_BASIS_UNRESOLVED")
        if current.primary_intent is None:
            raise ValueError("PREDICTION_INTENT_REQUIRED")
        artifact = self._artifacts.read_artifact(measurement_contract_ref)
        if (
            artifact is None
            or artifact.project_id != current.project_id
            or artifact.cutoff_state != CutoffState.ELIGIBLE
        ):
            raise ValueError("MEASUREMENT_CONTRACT_SOURCE_UNAVAILABLE")
        raw = self._objects.read(artifact.byte_sha256)
        if len(raw) > 65_536 or hashlib.sha256(raw).hexdigest() != artifact.byte_sha256:
            raise ValueError("MEASUREMENT_CONTRACT_SOURCE_INVALID")
        contract = ResearchMeasurementContract.model_validate_json(raw)
        expected = ExpectedRange.model_validate(expected_outcome)
        if conditions != contract.conditions or (expected.measure, expected.unit) != (
            contract.measure,
            contract.unit,
        ):
            raise ValueError("PREDICTION_MEASUREMENT_CONTRACT_MISMATCH")
        spans = tuple(
            item
            for item in self._artifacts.list_evidence(current.project_id)
            if item.artifact_id == artifact.artifact_id
        )
        if not spans or any(item.cutoff_state != CutoffState.ELIGIBLE for item in spans):
            raise ValueError("MEASUREMENT_CONTRACT_EVIDENCE_INELIGIBLE")
        return PreparedPredictionBasis(
            contract,
            artifact.byte_sha256,
            artifact.artifact_id,
            tuple(item.span_id for item in spans),
            current.revision_digest,
            hypothesis_semantic_digest(current.model_dump(mode="python")),
            project.revision,
            knowledge_cutoff,
            expected,
        )

    def seal_prediction(
        self,
        current: HypothesisRecord,
        prediction: PredictionRecord,
        basis: PreparedPredictionBasis,
    ) -> None:
        project = self._projects.read(current.project_id)
        artifact = self._artifacts.read_artifact(basis.contract_ref)
        if (
            project is None
            or project.revision != basis.project_revision
            or self._ledger.read_heads(current.project_id).get(
                f"HYPOTHESIS:{current.hypothesis_id}"
            )
            != basis.hypothesis_digest
            or artifact is None
            or artifact.byte_sha256 != basis.contract_digest
            or artifact.cutoff_state != CutoffState.ELIGIBLE
            or prediction.knowledge_cutoff != basis.knowledge_cutoff
            or prediction.conditions != basis.contract.conditions
            or ExpectedRange.model_validate(prediction.expected_outcome) != basis.expected_outcome
        ):
            raise ValueError("PREDICTION_BASIS_CHANGED")
        seal_research_test_record(
            self._ledger, self._clock, self._ids, prediction, evidence_refs=basis.source_refs
        )

    def require_prediction(self, prediction: PredictionRecord, current: HypothesisRecord) -> None:
        if (
            prediction.project_id != current.project_id
            or prediction.hypothesis_id != current.hypothesis_id
            or prediction.object_id != current.object_id
            or prediction.prediction_id not in current.prediction_refs
            or prediction.hypothesis_semantic_digest is None
            or prediction.hypothesis_semantic_digest
            != hypothesis_semantic_digest(current.model_dump(mode="python"))
        ):
            raise ValueError("PREDICTION_HYPOTHESIS_BASIS_MISMATCH")
        self._require_sealed(
            prediction.project_id,
            prediction.prediction_id,
            prediction.prediction_digest,
            prediction.model_dump(mode="python"),
        )
        source = self._artifacts.read_artifact(prediction.measurement_contract_ref)
        if (
            source is None
            or source.project_id != current.project_id
            or source.byte_sha256 != prediction.measurement_contract_digest
            or source.cutoff_state != CutoffState.ELIGIBLE
        ):
            raise ValueError("PREDICTION_MEASUREMENT_SOURCE_CHANGED")

    def require_assessment(self, project_id: str, assessment_id: str) -> TestValidityAssessment:
        assessment = self._assessments.read_assessment(project_id, assessment_id)
        if assessment is None:
            raise ValueError("TEST_VALIDITY_PRODUCER_NOT_FOUND")
        expected_digest = domain_digest(
            "TEST_VALIDITY_ASSESSMENT",
            "1.0.0",
            canonical_payload(assessment.model_dump(mode="python", exclude={"assessment_digest"})),
        )
        if expected_digest != assessment.assessment_digest:
            raise ValueError("TEST_VALIDITY_DIGEST_MISMATCH")
        self._require_sealed(
            project_id,
            assessment.assessment_id,
            assessment.assessment_digest,
            assessment.model_dump(mode="python"),
        )
        execution = self._executions.read_execution(project_id, assessment.execution_ref)
        attempt = self._executions.read_attempt(project_id, assessment.attempt_ref)
        if (
            execution is None
            or attempt is None
            or execution.object_id != assessment.object_id
            or execution.plan_id != assessment.plan_id
            or execution.plan_revision_digest != assessment.plan_revision_digest
            or attempt.plan_execution_id != execution.plan_execution_id
            or attempt.plan_revision_digest != assessment.plan_revision_digest
            or attempt.observation_refs != assessment.observation_refs
        ):
            raise ValueError("TEST_VALIDITY_EXECUTION_BINDING_MISMATCH")
        self._require_execution_owner(project_id, execution, attempt)
        if attempt.observation_completeness == "NOT_ADMITTED" or (
            assessment.substantive_update_allowed and attempt.state != "SUCCEEDED"
        ):
            raise ValueError("TEST_VALIDITY_EXECUTION_NOT_ADMITTED")
        for reference in assessment.observation_refs:
            span = self._artifacts.read_evidence(reference)
            if (
                span is None
                or span.project_id != project_id
                or span.cutoff_state != CutoffState.ELIGIBLE
            ):
                raise ValueError("TEST_VALIDITY_OBSERVATION_INELIGIBLE")
            artifact = self._artifacts.read_artifact(span.artifact_id)
            if (
                artifact is None
                or artifact.byte_sha256 != assessment.raw_observation_digest
                or artifact.source_uri != f"sandbox://{assessment.attempt_ref}/result"
            ):
                raise ValueError("TEST_VALIDITY_OBSERVATION_ORIGIN_MISMATCH")
        return assessment

    def _require_execution_owner(
        self,
        project_id: str,
        execution: PlanExecutionRecord,
        attempt: StepExecutionAttemptRecord,
    ) -> None:
        revision = self._ledger.read_revision_by_digest(project_id, execution.revision_digest)
        snapshot = None if revision is None else self._ledger.read_snapshot(revision.snapshot_id)
        content = execution.model_dump(mode="python")
        if (
            revision is None
            or snapshot is None
            or revision.entity_type != EntityType.EXECUTION
            or revision.entity_id != execution.plan_execution_id
            or self._ledger.read_heads(project_id).get(f"EXECUTION:{execution.plan_execution_id}")
            != execution.revision_digest
            or canonical_payload(snapshot.content) != canonical_payload(content)
            or snapshot.content_digest
            != domain_digest("ENTITY_SNAPSHOT", "1.0.0", canonical_payload(content))
        ):
            raise ValueError("TEST_EXECUTION_CANONICAL_PROJECTION_MISMATCH")
        expected = domain_digest(
            "STEP_EXECUTION_ATTEMPT",
            "1.0.0",
            canonical_payload(attempt.model_dump(mode="python", exclude={"revision_digest"})),
        )
        if expected != attempt.revision_digest or attempt.attempt_id not in execution.attempt_refs:
            raise ValueError("TEST_EXECUTION_ATTEMPT_DIGEST_MISMATCH")

    def validate_binding(
        self,
        prediction: PredictionRecord,
        *,
        execution_ref: str,
        observation_refs: tuple[str, ...],
        assessment_ref: str,
        test_validity: str,
        prediction_fit: str,
    ) -> TestValidityAssessment:
        assessment = self.require_assessment(prediction.project_id, assessment_ref)
        current = self._hypotheses.read_hypothesis(
            prediction.project_id, prediction.hypothesis_id, None
        )
        if current is None:
            raise ValueError("TEST_HYPOTHESIS_NOT_FOUND")
        self.require_prediction(prediction, current)
        if (
            assessment.prediction_id != prediction.prediction_id
            or assessment.prediction_digest != prediction.prediction_digest
            or assessment.execution_ref != execution_ref
            or assessment.observation_refs != observation_refs
            or assessment.test_validity != test_validity
            or assessment.prediction_fit != prediction_fit
        ):
            raise ValueError("TEST_BINDING_PRODUCER_MISMATCH")
        return assessment

    def validate_appraisal(
        self,
        current: HypothesisRecord,
        bindings: tuple[HypothesisTestBinding, ...],
        evidence_refs: tuple[str, ...],
        appraisal_scope: dict[str, str],
    ) -> None:
        for binding in bindings:
            prediction = self._hypotheses.read_prediction(binding.prediction_id)
            if prediction is None:
                raise ValueError("APPRAISAL_PREDICTION_NOT_FOUND")
            self.require_prediction(prediction, current)
            assessment = self.validate_binding(
                prediction,
                execution_ref=binding.execution_ref,
                observation_refs=binding.observation_refs,
                assessment_ref=binding.test_validity_assessment_ref,
                test_validity=binding.test_validity,
                prediction_fit=binding.prediction_fit,
            )
            if (
                binding.assessment_digest != assessment.assessment_digest
                or binding.prediction_digest != prediction.prediction_digest
                or binding.appraisal_mutation_allowed != assessment.substantive_update_allowed
                or not set(assessment.observation_refs).issubset(evidence_refs)
                or any(
                    appraisal_scope.get(key) != expected
                    for key, expected in assessment.scope.items()
                )
            ):
                raise ValueError("APPRAISAL_TEST_BINDING_MISMATCH")

    def _require_sealed(
        self, project_id: str, identifier: str, digest: str, content: dict[str, object]
    ) -> None:
        revision = self._ledger.read_revision_by_digest(project_id, digest)
        snapshot = None if revision is None else self._ledger.read_snapshot(revision.snapshot_id)
        if (
            revision is None
            or snapshot is None
            or revision.entity_type != EntityType.HYPOTHESIS
            or revision.entity_id != identifier
            or self._ledger.read_heads(project_id).get(f"HYPOTHESIS:{identifier}") != digest
            or canonical_payload(snapshot.content) != canonical_payload(content)
            or snapshot.content_digest
            != domain_digest("ENTITY_SNAPSHOT", "1.0.0", canonical_payload(content))
        ):
            raise ValueError("RESEARCH_TEST_RECORD_NOT_SEALED")
