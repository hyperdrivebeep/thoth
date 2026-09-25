"""Resolve specialized historical references through their actual typed owners."""

from collections.abc import Callable

from thoth.domain.action_full import ActionPlanRecord
from thoth.domain.base import DomainModel
from thoth.domain.hypothesis_full import (
    HypothesisPortfolioRecord,
    HypothesisRecord,
    PredictionRecord,
)
from thoth.domain.resource_scope import ResourceScopeError
from thoth.domain.restore import RestoreError, RestoreReferenceBasis
from thoth.domain.revision import SemanticRevision
from thoth.domain.test_validity import hypothesis_semantic_digest
from thoth.ports.action import ActionStorePort
from thoth.ports.execution import ExecutionStorePort
from thoth.ports.hypothesis import HypothesisStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.project import ProjectStorePort
from thoth.ports.resource_scope import ResourceAccessPort
from thoth.ports.test_validity import TestValidityStorePort


class RestoreReferenceRegistry:
    def __init__(
        self,
        readers: dict[
            type[DomainModel], Callable[[SemanticRevision, DomainModel], RestoreReferenceBasis]
        ],
    ) -> None:
        self.readers = readers

    def resolve(self, target: SemanticRevision, record: DomainModel) -> RestoreReferenceBasis:
        reader = self.readers.get(type(record))
        if reader is not None:
            try:
                return reader(target, record)
            except ResourceScopeError as exc:
                raise RestoreError("RESTORE_DEPENDENT_SCOPE_DENIED") from exc
        values = record.model_dump(mode="python")
        if any(
            values.get(key)
            for key in (
                "prediction_refs",
                "test_refs",
                "assumption_refs",
                "relation_refs",
                "authorization_refs",
            )
        ):
            raise RestoreError("RESTORE_IMPACT_INCOMPLETE")
        return RestoreReferenceBasis()


class HypothesisRestoreReferences:
    def __init__(
        self,
        hypotheses: HypothesisStorePort,
        tests: TestValidityStorePort,
        ledger: LedgerPort,
        access: ResourceAccessPort,
        projects: ProjectStorePort,
        executions: ExecutionStorePort,
    ) -> None:
        self.hypotheses, self.tests, self.ledger, self.access, self.projects = (
            hypotheses,
            tests,
            ledger,
            access,
            projects,
        )
        self.executions = executions

    def _canonical(self, project: str, identifier: str, digest: str, heads: dict[str, str]) -> None:
        key = f"HYPOTHESIS:{identifier}"
        current = self.ledger.read_heads(project).get(key)
        if current is not None:
            if current != digest:
                raise RestoreError("RESTORE_MEMBERS_REQUIRE_REVIEW")
            self.access.require_revision(project, digest)
            heads[key] = digest

    def hypothesis(self, target: SemanticRevision, value: DomainModel) -> RestoreReferenceBasis:
        if not isinstance(value, HypothesisRecord):
            raise RestoreError("RESTORE_TARGET_MISMATCH")
        project = self.projects.read(target.project_id)
        if project is None:
            raise RestoreError("RESTORE_ACCESS_DENIED")
        records: dict[str, dict[str, object]] = {}
        heads: dict[str, str] = {}
        assumptions = {
            item.assumption_id: item
            for item in self.hypotheses.list_assumptions(target.project_id, value.hypothesis_id)
        }
        for identifier in value.assumption_refs:
            assumption = assumptions.get(identifier)
            if (
                assumption is None
                or assumption.project_id != target.project_id
                or assumption.hypothesis_id != value.hypothesis_id
            ):
                raise RestoreError("RESTORE_IMPACT_INCOMPLETE")
            self.access.require_reads(target.project_id, assumption.evidence_refs)
            records[identifier] = assumption.model_dump(mode="python")
        semantic = hypothesis_semantic_digest(value.model_dump(mode="python"))
        predictions: dict[str, PredictionRecord] = {}
        for identifier in value.prediction_refs:
            prediction = self.hypotheses.read_prediction(identifier)
            if (
                prediction is None
                or prediction.project_id != target.project_id
                or prediction.hypothesis_id != value.hypothesis_id
            ):
                raise RestoreError("RESTORE_TARGET_MISMATCH")
            if (
                prediction.object_id not in {None, value.object_id}
                or prediction.hypothesis_semantic_digest != semantic
            ):
                raise RestoreError("RESTORE_MEMBERS_REQUIRE_REVIEW")
            if prediction.knowledge_cutoff != project.cutoff_at or not set(
                prediction.assumption_refs
            ).issubset(value.assumption_refs):
                raise RestoreError("RESTORE_MEMBERS_REQUIRE_REVIEW")
            parent = self.ledger.read_revision_by_digest(
                target.project_id, prediction.hypothesis_revision_digest
            )
            if (
                parent is None
                or parent.entity_id != value.hypothesis_id
                or parent.entity_type.value != "HYPOTHESIS"
            ):
                raise RestoreError("RESTORE_TARGET_MISMATCH")
            self.access.require_revision(target.project_id, parent.revision_digest)
            self._canonical(target.project_id, identifier, prediction.prediction_digest, heads)
            records[identifier] = prediction.model_dump(mode="python")
            predictions[identifier] = prediction
        bindings = {
            binding.test_binding_id: binding
            for binding in self.hypotheses.list_test_bindings(target.project_id, None)
        }
        for identifier in value.test_refs:
            binding = bindings.get(identifier)
            if binding is None or binding.project_id != target.project_id:
                raise RestoreError("RESTORE_IMPACT_INCOMPLETE")
            assessment = self.tests.read_assessment(
                target.project_id, binding.test_validity_assessment_ref
            )
            if assessment is None:
                raise RestoreError("RESTORE_IMPACT_INCOMPLETE")
            prediction = predictions.get(assessment.prediction_id)
            if (
                assessment.project_id != target.project_id
                or assessment.object_id != value.object_id
                or assessment.hypothesis_id != value.hypothesis_id
                or assessment.hypothesis_semantic_digest != semantic
                or prediction is None
                or assessment.prediction_digest != prediction.prediction_digest
                or binding.prediction_id != assessment.prediction_id
                or binding.execution_ref != assessment.execution_ref
                or binding.observation_refs != assessment.observation_refs
                or binding.test_validity != assessment.test_validity
                or binding.prediction_fit != assessment.prediction_fit
            ):
                raise RestoreError("RESTORE_TARGET_MISMATCH")
            if assessment.knowledge_cutoff != project.cutoff_at:
                raise RestoreError("RESTORE_SOURCE_DRIFT")
            plan = self.ledger.read_revision_by_digest(
                target.project_id, assessment.plan_revision_digest
            )
            plan_snapshot = None if plan is None else self.ledger.read_snapshot(plan.snapshot_id)
            execution = self.executions.read_execution(target.project_id, assessment.execution_ref)
            attempt = self.executions.read_attempt(target.project_id, assessment.attempt_ref)
            if (
                plan is None
                or plan_snapshot is None
                or plan.entity_type.value != "ACTION"
                or plan.entity_id != assessment.plan_id
                or plan_snapshot.content.get("object_id") != value.object_id
                or execution is None
                or execution.object_id != value.object_id
                or execution.plan_id != assessment.plan_id
                or attempt is None
                or attempt.plan_execution_id != execution.plan_execution_id
                or attempt.plan_revision_digest != assessment.plan_revision_digest
            ):
                raise RestoreError("RESTORE_TARGET_MISMATCH")
            self.access.require_revision(target.project_id, plan.revision_digest)
            self.access.require_revision(target.project_id, execution.revision_digest)
            self.access.require_reads(target.project_id, assessment.observation_refs)
            self._canonical(
                target.project_id, assessment.assessment_id, assessment.assessment_digest, heads
            )
            records[identifier] = binding.model_dump(mode="python")
            records[assessment.assessment_id] = assessment.model_dump(mode="python")
            records[assessment.execution_ref] = {"revision_digest": execution.revision_digest}
            records[assessment.attempt_ref] = {"revision_digest": attempt.revision_digest}
        return RestoreReferenceBasis(expected_heads=heads, records=records)

    def portfolio(self, target: SemanticRevision, value: DomainModel) -> RestoreReferenceBasis:
        if not isinstance(value, HypothesisPortfolioRecord):
            raise RestoreError("RESTORE_TARGET_MISMATCH")
        records: dict[str, dict[str, object]] = {}
        for identifier in value.relation_refs:
            relation = self.hypotheses.read_relation(identifier)
            if (
                relation is None
                or relation.project_id != target.project_id
                or relation.portfolio_id != value.portfolio_id
            ):
                raise RestoreError("RESTORE_TARGET_MISMATCH")
            if not {relation.source_hypothesis_id, relation.target_hypothesis_id}.issubset(
                value.hypothesis_refs
            ):
                raise RestoreError("RESTORE_TARGET_MISMATCH")
            if not relation.active or relation.supersedes_revision_digest is not None:
                raise RestoreError("RESTORE_MEMBERS_REQUIRE_REVIEW")
            self.access.require_reads(target.project_id, relation.evidence_refs)
            records[identifier] = relation.model_dump(mode="python")
        return RestoreReferenceBasis(records=records)


class PlanRestoreReferences:
    def __init__(
        self, actions: ActionStorePort, ledger: LedgerPort, access: ResourceAccessPort
    ) -> None:
        self.actions, self.ledger, self.access = actions, ledger, access

    def resolve(self, target: SemanticRevision, value: DomainModel) -> RestoreReferenceBasis:
        if not isinstance(value, ActionPlanRecord):
            raise RestoreError("RESTORE_TARGET_MISMATCH")
        records: dict[str, dict[str, object]] = {}
        heads: dict[str, str] = {}
        for identifier in value.authorization_refs:
            authorization = self.actions.read_authorization(target.project_id, identifier)
            if (
                authorization is None
                or authorization.project_id != target.project_id
                or authorization.plan_id != value.plan_id
                or authorization.step_id not in {str(step.get("step_id")) for step in value.steps}
            ):
                raise RestoreError("RESTORE_TARGET_MISMATCH")
            parent = self.ledger.read_revision_by_digest(
                target.project_id, authorization.plan_revision_digest
            )
            if (
                parent is None
                or parent.entity_type.value != "ACTION"
                or parent.entity_id != value.plan_id
            ):
                raise RestoreError("RESTORE_TARGET_MISMATCH")
            self.access.require_revision(target.project_id, authorization.revision_digest)
            self.access.require_revision(target.project_id, parent.revision_digest)
            heads[f"ACTION:{identifier}"] = authorization.revision_digest
            # Past approval/effect state is only provenance. It is not copied to a new grant.
            records[identifier] = authorization.model_dump(mode="python")
        return RestoreReferenceBasis(expected_heads=heads, records=records)
