from __future__ import annotations

from collections.abc import Iterable
from contextlib import AbstractContextManager
from datetime import datetime

from thoth.application.services.hypothesis_test_lifecycle import (
    HypothesisTestLifecycle,
    PreparedPredictionBasis,
)
from thoth.application.services.revision_service import CommitResult, RevisionCommitService
from thoth.domain.actor import ActorRef
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.decision_object_full import DecisionObjectRecord
from thoth.domain.enums import ActorKind, CutoffState, EntityType
from thoth.domain.hypothesis_full import (
    AuxiliaryAssumptionRecord,
    HypothesisAppraisalRecord,
    HypothesisAuditRecord,
    HypothesisPortfolioRecord,
    HypothesisRecord,
    HypothesisRelationRecord,
    HypothesisTestBinding,
    PredictionRecord,
)
from thoth.domain.revision import (
    EntitySnapshot,
    ImpactPropagationPlan,
    RevisionChangeSet,
    SemanticRevision,
    StagedRevision,
)
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.decision_object import DecisionObjectStorePort
from thoth.ports.hypothesis import HypothesisStorePort
from thoth.ports.ledger import LedgerPort, LedgerTransactionPort
from thoth.ports.runtime import ClockPort, IdGeneratorPort

PRIMARY_INTENTS = {
    "DIAGNOSTIC_CAUSAL",
    "EXPLANATORY_MECHANISTIC",
    "PREDICTIVE",
    "INTERVENTION_DESIGN",
    "EXPLORATORY",
}
CAUSAL_INTENTS = {"DIAGNOSTIC_CAUSAL", "EXPLANATORY_MECHANISTIC"}
RELATION_TYPES = {
    "ALTERNATIVE_TO",
    "COMPATIBLE_WITH",
    "CONTRIBUTES_WITH",
    "SPECIALIZES",
    "CAUSALLY_UPSTREAM_OF",
    "DUPLICATE_CANDIDATE",
}
QUALITY_AXES = (
    "clarity_atomicity",
    "problem_scope_alignment",
    "evidence_basis",
    "counterevidence_readiness",
    "causal_coherence",
    "assumption_visibility",
    "prediction_specificity",
    "measurement_alignment",
    "testability",
    "discrimination",
    "boundary_calibration",
)


class HypothesisService:
    def __init__(
        self,
        *,
        store: HypothesisStorePort,
        objects: DecisionObjectStorePort,
        artifacts: ArtifactLedgerPort,
        ledger: LedgerPort,
        commits: RevisionCommitService,
        clock: ClockPort,
        ids: IdGeneratorPort,
        test_lifecycle: HypothesisTestLifecycle | None = None,
    ) -> None:
        self._store = store
        self._objects = objects
        self._artifacts = artifacts
        self._ledger = ledger
        self._commits = commits
        self._clock = clock
        self._ids = ids
        self._test_lifecycle = test_lifecycle

    def transaction(self) -> AbstractContextManager[LedgerTransactionPort]:
        return self._ledger.transaction()

    def create(
        self,
        *,
        project_id: str,
        object_id: str,
        portfolio_id: str,
        statement: str,
        primary_intent: str,
        secondary_intents: tuple[str, ...],
        evidence_basis: str,
        scope: dict[str, str],
        evidence_refs: tuple[str, ...],
        prespecification_state: str = "UNKNOWN",
        expected_object_revision: str | None = None,
    ) -> tuple[HypothesisRecord, tuple[str, ...], CommitResult]:
        with self._ledger.transaction():
            object_record = self._check_object_head(project_id, object_id, expected_object_revision)
            self._validate_intents(primary_intent, secondary_intents)
            self._validate_evidence(project_id, evidence_refs)
            duplicates = tuple(
                item.hypothesis_id
                for item in self._store.list_hypotheses(project_id)
                if item.object_id == object_id
                and item.freshness != "INVALIDATED"
                and item.statement.casefold() == statement.casefold()
            )
            grounded = bool(evidence_refs and evidence_basis.strip() and scope)
            gateway = {
                "atomic_statement": "PASS" if statement.strip() else "FAIL",
                "observed_problem_scope": "PASS" if scope else "FAIL",
                "intent_profile": "PASS",
                "source_cutoff_provenance": "PASS" if evidence_refs else "INCOMPLETE",
                "causal_blocker_separation": "PASS",
                "evidence_basis_label": "PASS" if evidence_basis else "FAIL",
                "expected_outcome_model": "PENDING",
                "observable_test_route": "PENDING",
                "future_oracle_leakage": "PASS",
            }
            quality = {
                axis: (
                    "MIXED"
                    if axis in {"clarity_atomicity", "problem_scope_alignment", "evidence_basis"}
                    and grounded
                    else "NOT_ASSESSED"
                )
                for axis in QUALITY_AXES
            }
            draft: dict[str, object] = {
                "hypothesis_revision_id": self._ids.new("hypothesis-revision"),
                "hypothesis_id": self._ids.new("hypothesis"),
                "project_id": project_id,
                "object_id": object_id,
                "portfolio_id": portfolio_id,
                "statement": statement,
                "observed_problem": object_record.problem_frame,
                "primary_intent": primary_intent,
                "secondary_intents": secondary_intents,
                "intent_profile_refs": (f"intent-profile:{primary_intent.lower()}:1",),
                "evidence_basis": evidence_basis,
                "scope": scope,
                "evidence_refs": evidence_refs,
                "causal_profile": (
                    {"applicability": "REQUIRED", "primary_locus": "UNCLASSIFIED"}
                    if primary_intent in CAUSAL_INTENTS
                    else {"applicability": "NOT_APPLICABLE"}
                ),
                "development_stage": "GROUNDED_CANDIDATE" if grounded else "DRAFT",
                "empirical_appraisal": "UNASSESSED",
                "freshness": "CURRENT",
                "prespecification_state": prespecification_state,
                "quality_profile": quality,
                "gateway_results": gateway,
                "created_at": self._clock.now(),
            }
            record = HypothesisRecord.model_validate(
                {**draft, "revision_digest": self._digest(draft)}
            )
            committed, commit = self._persist_hypothesis(record, "hypothesis/created")
            return committed, duplicates, commit

    def revise(
        self,
        current: HypothesisRecord,
        *,
        updates: dict[str, object],
        event_type: str,
        evidence_refs: tuple[str, ...] = (),
        invalidate_derived: bool = False,
    ) -> tuple[HypothesisRecord, CommitResult]:
        self._validate_evidence(current.project_id, evidence_refs)
        draft = current.model_dump(mode="python")
        invalidated = current.invalidated_refs
        if invalidate_derived:
            invalidated = tuple(
                dict.fromkeys(
                    (*invalidated, *current.prediction_refs, *current.test_refs, "APPRAISALS")
                )
            )
            updates = {
                **updates,
                "prediction_refs": (),
                "test_refs": (),
                "invalidated_refs": invalidated,
                "empirical_appraisal": "UNASSESSED",
                "freshness": "CURRENT",
                "development_stage": (
                    "GROUNDED_CANDIDATE"
                    if current.evidence_refs
                    and updates.get("primary_intent", current.primary_intent) is not None
                    else "DRAFT"
                ),
            }
        draft.update(updates)
        draft.update(
            {
                "hypothesis_revision_id": self._ids.new("hypothesis-revision"),
                "supersedes_revision_digest": current.revision_digest,
                "created_at": self._clock.now(),
            }
        )
        draft.pop("revision_digest", None)
        draft.pop("receipt_ref", None)
        revised = HypothesisRecord.model_validate({**draft, "revision_digest": self._digest(draft)})
        return self._persist_hypothesis(revised, event_type, evidence_refs=evidence_refs)

    def compose_portfolio(
        self,
        *,
        project_id: str,
        object_id: str,
        portfolio_id: str | None,
        hypothesis_ids: tuple[str, ...],
        unknown_reserve: dict[str, object],
        expected_object_revision: str | None = None,
    ) -> tuple[HypothesisPortfolioRecord, CommitResult]:
        with self._ledger.transaction():
            self._check_object_head(project_id, object_id, expected_object_revision)
            hypotheses = self._hypotheses(project_id, hypothesis_ids)
            if any(item.object_id != object_id for item in hypotheses):
                raise ValueError("portfolio cannot cross DecisionObject boundaries")
            if len(hypotheses) < 2 and not (
                unknown_reserve.get("alternatives_considered")
                and unknown_reserve.get("next_checks")
            ):
                raise ValueError("0/1 portfolio requires alternative review and next checks")
            if not unknown_reserve:
                raise ValueError("portfolio requires an explicit unknown reserve")
            effective_id = portfolio_id or self._ids.new("hypothesis-portfolio")
            current = self._store.read_portfolio(project_id, effective_id, None)
            statements = {item.statement.casefold() for item in hypotheses}
            quality_gaps = tuple(
                gap
                for gap, present in (
                    (
                        "semantic diversity",
                        len(hypotheses) > 1 and len(statements) == len(hypotheses),
                    ),
                    (
                        "counterevidence route",
                        bool(hypotheses)
                        and all(item.counterevidence_queries for item in hypotheses),
                    ),
                    (
                        "prediction coverage",
                        bool(hypotheses) and all(item.prediction_refs for item in hypotheses),
                    ),
                )
                if not present
            )
            draft: dict[str, object] = {
                "portfolio_revision_id": self._ids.new("portfolio-revision"),
                "portfolio_id": effective_id,
                "project_id": project_id,
                "object_id": object_id,
                "hypothesis_refs": hypothesis_ids,
                "relation_refs": (() if current is None else current.relation_refs),
                "unknown_reserve": unknown_reserve,
                "coverage_state": "NOT_ASSESSED"
                if not hypotheses
                else "PARTIAL"
                if quality_gaps
                else "COVERED",
                "diversity_state": (
                    "NOT_ASSESSED"
                    if len(hypotheses) < 2
                    else "DIVERSE"
                    if len(statements) == len(hypotheses)
                    else "PARAPHRASE_RISK"
                ),
                "discrimination_state": (
                    "NOT_ASSESSED"
                    if not hypotheses
                    else "CANDIDATE"
                    if all(item.prediction_refs for item in hypotheses)
                    else "MISSING"
                ),
                "abstention_state": "AVAILABLE",
                "quality_gaps": quality_gaps,
                "discrimination_matrix": (),
                "supersedes_revision_digest": (
                    None if current is None else current.revision_digest
                ),
                "created_at": self._clock.now(),
            }
            record = HypothesisPortfolioRecord.model_validate(
                {**draft, "revision_digest": self._portfolio_digest(draft)}
            )
            return self._persist_portfolio(record, "hypothesis/portfolioUpdated")

    def revalidate_portfolio(
        self, current: HypothesisPortfolioRecord, trigger_reason: str
    ) -> tuple[HypothesisPortfolioRecord, CommitResult]:
        with self._ledger.transaction():
            if (
                self._ledger.read_heads(current.project_id).get(
                    f"HYPOTHESIS:{current.portfolio_id}"
                )
                != current.revision_digest
            ):
                raise ValueError("HYPOTHESIS_REVISION_CONFLICT")
            record, commit = self.compose_portfolio(
                project_id=current.project_id,
                object_id=current.object_id,
                portfolio_id=current.portfolio_id,
                hypothesis_ids=current.hypothesis_refs,
                unknown_reserve=current.unknown_reserve,
            )
            self.audit(
                record.project_id,
                record.portfolio_id,
                "hypothesis/portfolioUpdated",
                {"trigger_reason": trigger_reason},
            )
            return record, commit

    def add_relation(
        self,
        current: HypothesisPortfolioRecord,
        *,
        source_hypothesis_id: str,
        relation_type: str,
        target_hypothesis_id: str,
        evidence_refs: tuple[str, ...],
        semantic_role: str | None,
    ) -> tuple[HypothesisRelationRecord, HypothesisPortfolioRecord, CommitResult]:
        if relation_type not in RELATION_TYPES:
            raise ValueError("unsupported hypothesis relation type")
        self._hypotheses(current.project_id, (source_hypothesis_id, target_hypothesis_id))
        self._validate_evidence(current.project_id, evidence_refs)
        draft: dict[str, object] = {
            "relation_revision_id": self._ids.new("hypothesis-relation-revision"),
            "relation_id": self._ids.new("hypothesis-relation"),
            "project_id": current.project_id,
            "portfolio_id": current.portfolio_id,
            "source_hypothesis_id": source_hypothesis_id,
            "relation_type": relation_type,
            "target_hypothesis_id": target_hypothesis_id,
            "evidence_refs": evidence_refs,
            "semantic_role": semantic_role,
            "authority_state": "UNCLASSIFIED",
            "active": True,
            "created_at": self._clock.now(),
        }
        relation = HypothesisRelationRecord.model_validate(
            {**draft, "revision_digest": self._relation_digest(draft)}
        )
        with self._ledger.transaction():
            self._store.add_relation(relation)
            revised, commit = self._revise_portfolio(
                current,
                updates={"relation_refs": (*current.relation_refs, relation.relation_id)},
                event_type="hypothesis/relationChanged",
            )
            return relation, revised, commit

    def end_relation(
        self,
        current: HypothesisPortfolioRecord,
        relation: HypothesisRelationRecord,
        *,
        reason: str,
        evidence_refs: tuple[str, ...],
    ) -> tuple[HypothesisRelationRecord, HypothesisPortfolioRecord, CommitResult]:
        self._validate_evidence(current.project_id, evidence_refs)
        if not relation.active:
            raise ValueError("hypothesis relation is already inactive")
        draft = relation.model_dump(mode="python")
        draft.update(
            {
                "relation_revision_id": self._ids.new("hypothesis-relation-revision"),
                "active": False,
                "supersedes_revision_digest": relation.revision_digest,
                "created_at": self._clock.now(),
            }
        )
        draft.pop("revision_digest", None)
        ended = HypothesisRelationRecord.model_validate(
            {**draft, "revision_digest": self._relation_digest(draft)}
        )
        with self._ledger.transaction():
            self._store.add_relation(ended)
            revised, commit = self._revise_portfolio(
                current,
                updates={"relation_refs": current.relation_refs},
                event_type="hypothesis/relationChanged",
            )
            self.audit(
                current.project_id,
                relation.source_hypothesis_id,
                "hypothesis/relationChanged",
                {"reason": reason, "relation_id": relation.relation_id},
            )
            return ended, revised, commit

    def add_assumption(
        self,
        current: HypothesisRecord,
        *,
        statement: str,
        role: str,
        evidence_refs: tuple[str, ...],
        validation_route: str | None,
    ) -> tuple[AuxiliaryAssumptionRecord, HypothesisRecord, CommitResult]:
        self._validate_evidence(current.project_id, evidence_refs)
        draft: dict[str, object] = {
            "assumption_id": self._ids.new("hypothesis-assumption"),
            "project_id": current.project_id,
            "hypothesis_id": current.hypothesis_id,
            "statement": statement,
            "role": role,
            "evidence_refs": evidence_refs,
            "validation_route": validation_route,
            "status": "UNVALIDATED",
            "testability": "TESTABLE" if validation_route else "NOT_ASSESSED",
            "created_at": self._clock.now(),
        }
        assumption = AuxiliaryAssumptionRecord.model_validate(
            {**draft, "assumption_digest": self._aux_digest("ASSUMPTION", draft)}
        )
        with self._ledger.transaction():
            self._store.add_assumption(assumption)
            revised, commit = self.revise(
                current,
                updates={"assumption_refs": (*current.assumption_refs, assumption.assumption_id)},
                event_type="hypothesis/assumptionChanged",
                evidence_refs=evidence_refs,
                invalidate_derived=True,
            )
            return assumption, revised, commit

    def bind_prediction(
        self,
        current: HypothesisRecord,
        *,
        knowledge_cutoff: datetime,
        prespecification_state: str,
        conditions: dict[str, str],
        measurement_contract_ref: str,
        assumption_refs: tuple[str, ...],
        expected_outcome: dict[str, object],
        discrimination_map: dict[str, object],
        prepared_basis: PreparedPredictionBasis | None = None,
    ) -> tuple[PredictionRecord, HypothesisRecord, CommitResult]:
        if not set(assumption_refs).issubset(set(current.assumption_refs)):
            raise ValueError("prediction references an unknown auxiliary assumption")
        if self._test_lifecycle is None:
            raise ValueError("RESEARCH_TEST_LIFECYCLE_UNAVAILABLE")
        basis = prepared_basis or self._test_lifecycle.prepare_prediction(
            current,
            knowledge_cutoff=knowledge_cutoff,
            measurement_contract_ref=measurement_contract_ref,
            conditions=conditions,
            expected_outcome=expected_outcome,
        )
        draft: dict[str, object] = {
            "prediction_id": self._ids.new("prediction"),
            "project_id": current.project_id,
            "hypothesis_id": current.hypothesis_id,
            "hypothesis_revision_digest": current.revision_digest,
            "knowledge_cutoff": knowledge_cutoff,
            "prespecification_state": prespecification_state,
            "conditions": conditions,
            "population_or_object": conditions.get("population", current.object_id),
            "time_window": conditions.get("time_window", "BOUND_EXECUTION_INTERVAL"),
            "measurement_contract_ref": measurement_contract_ref,
            "assumption_refs": assumption_refs,
            "expected_outcome": expected_outcome,
            "discrimination_map": discrimination_map,
            "observation_bound": False,
            "prediction_fit": "NOT_OBSERVED",
            "created_at": self._clock.now(),
            "object_id": current.object_id,
            "measurement_contract": basis.contract,
            "measurement_contract_digest": basis.contract_digest,
            "hypothesis_semantic_digest": basis.semantic_digest,
            "unbound_assumption_candidates": ()
            if current.generation_details is None
            else current.generation_details.assumptions,
            "schema_version": "1.1.0",
        }
        prediction = PredictionRecord.model_validate({**draft, "prediction_digest": "0" * 64})
        prediction = prediction.model_copy(
            update={
                "prediction_digest": self._aux_digest(
                    "PREDICTION",
                    prediction.model_dump(mode="python", exclude={"prediction_digest"}),
                )
            }
        )
        with self._ledger.transaction():
            self._test_lifecycle.seal_prediction(current, prediction, basis)
            self._store.add_prediction(prediction)
            revised, commit = self.revise(
                current,
                updates={
                    "prediction_refs": (*current.prediction_refs, prediction.prediction_id),
                    "development_stage": "PREDICTION_BOUND",
                    "prespecification_state": prespecification_state,
                    "gateway_results": {**current.gateway_results, "sealed_prediction": "PASS"},
                },
                event_type="hypothesis/predictionBound",
            )
            return prediction, revised, commit

    def bind_test(
        self,
        prediction: PredictionRecord,
        *,
        execution_ref: str,
        observation_refs: tuple[str, ...],
        test_validity_assessment_ref: str,
        test_validity: str,
        prediction_fit: str,
    ) -> HypothesisTestBinding:
        self._validate_evidence(prediction.project_id, observation_refs)
        if self._test_lifecycle is None:
            raise ValueError("RESEARCH_TEST_LIFECYCLE_UNAVAILABLE")
        assessment = self._test_lifecycle.validate_binding(
            prediction,
            execution_ref=execution_ref,
            observation_refs=observation_refs,
            assessment_ref=test_validity_assessment_ref,
            test_validity=test_validity,
            prediction_fit=prediction_fit,
        )
        for previous in self._store.list_test_bindings(
            prediction.project_id, prediction.prediction_id
        ):
            if previous.test_validity_assessment_ref == assessment.assessment_id:
                if previous.assessment_digest != assessment.assessment_digest:
                    raise ValueError("TEST_BINDING_IDEMPOTENCY_CONFLICT")
                return previous
        draft: dict[str, object] = {
            "test_binding_id": self._ids.new("hypothesis-test-binding"),
            "project_id": prediction.project_id,
            "prediction_id": prediction.prediction_id,
            "execution_ref": execution_ref,
            "observation_refs": observation_refs,
            "test_validity_assessment_ref": test_validity_assessment_ref,
            "test_validity": test_validity,
            "prediction_fit": prediction_fit,
            "appraisal_mutation_allowed": assessment.substantive_update_allowed,
            "assessment_digest": assessment.assessment_digest,
            "prediction_digest": prediction.prediction_digest,
            "created_at": self._clock.now(),
        }
        binding = HypothesisTestBinding.model_validate(
            {**draft, "binding_digest": self._aux_digest("TEST_BINDING", draft)}
        )
        self._store.add_test_binding(binding)
        return binding

    def appraise(
        self,
        current: HypothesisRecord,
        *,
        evidence_refs: tuple[str, ...],
        test_assessment_refs: tuple[str, ...],
        appraisal_scope: dict[str, str],
    ) -> tuple[HypothesisAppraisalRecord, HypothesisRecord, CommitResult]:
        self._validate_evidence(current.project_id, evidence_refs)
        bindings = tuple(
            item
            for item in self._store.list_test_bindings(current.project_id, None)
            if item.test_binding_id in test_assessment_refs
        )
        if len(bindings) != len(test_assessment_refs):
            raise ValueError("appraisal references an unknown test assessment")
        if bindings:
            if self._test_lifecycle is None:
                raise ValueError("RESEARCH_TEST_LIFECYCLE_UNAVAILABLE")
            self._test_lifecycle.validate_appraisal(
                current, bindings, evidence_refs, appraisal_scope
            )
            if set(test_assessment_refs).intersection(current.test_refs):
                raise ValueError("TEST_RESULT_ALREADY_APPLIED")
        valid = bool(bindings) and all(item.appraisal_mutation_allowed for item in bindings)
        fits = {item.prediction_fit for item in bindings}
        appraisal = (
            "INCONCLUSIVE"
            if not valid
            else "EVIDENCE_FAVORS"
            if fits == {"MATCH"}
            else "EVIDENCE_AGAINST"
            if fits == {"MISMATCH"}
            else "MIXED"
        )
        rationale = (
            "Substantive appraisal withheld: no eligible confirmatory test chain"
            if not valid
            else "Scoped appraisal derived from sealed prediction-fit assessments"
        )
        draft: dict[str, object] = {
            "appraisal_id": self._ids.new("hypothesis-appraisal"),
            "project_id": current.project_id,
            "hypothesis_id": current.hypothesis_id,
            "hypothesis_revision_digest": current.revision_digest,
            "evidence_refs": evidence_refs,
            "test_assessment_refs": test_assessment_refs,
            "appraisal_scope": appraisal_scope,
            "appraisal": appraisal,
            "rationale": rationale,
            "boundaries": tuple(f"{key}={value}" for key, value in appraisal_scope.items()),
            "calibration": {
                "prespecification_state": current.prespecification_state,
                "prediction_count": len(current.prediction_refs),
            },
            "substantive_update_applied": valid,
            "created_at": self._clock.now(),
        }
        record = HypothesisAppraisalRecord.model_validate(
            {**draft, "appraisal_digest": self._aux_digest("APPRAISAL", draft)}
        )
        with self._ledger.transaction():
            self._store.add_appraisal(record)
            revised, commit = self.revise(
                current,
                updates={
                    "empirical_appraisal": appraisal if valid else current.empirical_appraisal,
                    "development_stage": "EMPIRICALLY_UPDATED"
                    if valid
                    else current.development_stage,
                    "test_refs": tuple(dict.fromkeys((*current.test_refs, *test_assessment_refs))),
                },
                event_type="hypothesis/appraisalUpdated",
                evidence_refs=evidence_refs,
            )
            return record, revised, commit

    def audit(
        self,
        project_id: str,
        hypothesis_id: str,
        event_type: str,
        payload: dict[str, object],
    ) -> HypothesisAuditRecord:
        created_at = self._clock.now()
        draft = {
            "project_id": project_id,
            "hypothesis_id": hypothesis_id,
            "event_type": event_type,
            "payload": payload,
            "created_at": created_at,
        }
        record = HypothesisAuditRecord(
            audit_id=self._ids.new("hypothesis-audit"),
            project_id=project_id,
            hypothesis_id=hypothesis_id,
            event_type=event_type,
            payload=payload,
            event_digest=domain_digest("HYPOTHESIS_AUDIT", "1.0.0", canonical_payload(draft)),
            created_at=created_at,
        )
        self._store.append_audit(record)
        return record

    def _persist_hypothesis(
        self,
        record: HypothesisRecord,
        event_type: str,
        *,
        evidence_refs: tuple[str, ...] = (),
    ) -> tuple[HypothesisRecord, CommitResult]:
        with self._ledger.transaction():
            commit = self._commit_record(
                record=record,
                entity_id=record.hypothesis_id,
                revision_id=record.hypothesis_revision_id,
                revision_digest=record.revision_digest,
                supersedes=record.supersedes_revision_digest,
                event_type=event_type,
                evidence_refs=evidence_refs or record.evidence_refs,
            )
            self._store.add_hypothesis(record)
            self.audit(
                record.project_id,
                record.hypothesis_id,
                event_type,
                {"revision_digest": record.revision_digest},
            )
            return record, commit

    def _persist_portfolio(
        self, record: HypothesisPortfolioRecord, event_type: str
    ) -> tuple[HypothesisPortfolioRecord, CommitResult]:
        with self._ledger.transaction():
            commit = self._commit_record(
                record=record,
                entity_id=record.portfolio_id,
                revision_id=record.portfolio_revision_id,
                revision_digest=record.revision_digest,
                supersedes=record.supersedes_revision_digest,
                event_type=event_type,
                evidence_refs=(),
            )
            self._store.add_portfolio(record)
            self.audit(
                record.project_id,
                record.portfolio_id,
                event_type,
                {"revision_digest": record.revision_digest},
            )
            return record, commit

    def _revise_portfolio(
        self,
        current: HypothesisPortfolioRecord,
        *,
        updates: dict[str, object],
        event_type: str,
    ) -> tuple[HypothesisPortfolioRecord, CommitResult]:
        draft = current.model_dump(mode="python")
        draft.update(updates)
        draft.update(
            {
                "portfolio_revision_id": self._ids.new("portfolio-revision"),
                "supersedes_revision_digest": current.revision_digest,
                "created_at": self._clock.now(),
            }
        )
        draft.pop("revision_digest", None)
        draft.pop("receipt_ref", None)
        revised = HypothesisPortfolioRecord.model_validate(
            {**draft, "revision_digest": self._portfolio_digest(draft)}
        )
        return self._persist_portfolio(revised, event_type)

    def _commit_record(
        self,
        *,
        record: HypothesisRecord | HypothesisPortfolioRecord,
        entity_id: str,
        revision_id: str,
        revision_digest: str,
        supersedes: str | None,
        event_type: str,
        evidence_refs: tuple[str, ...],
    ) -> CommitResult:
        if self._ledger.read_heads(record.project_id).get(f"HYPOTHESIS:{entity_id}") != supersedes:
            raise ValueError("HYPOTHESIS_REVISION_CONFLICT")
        content = record.model_dump(mode="python")
        snapshot = EntitySnapshot(
            snapshot_id=self._ids.new("snapshot"),
            project_id=record.project_id,
            entity_type=EntityType.HYPOTHESIS,
            entity_id=entity_id,
            schema_version="1.0.0",
            content=content,
            content_digest=domain_digest("ENTITY_SNAPSHOT", "1.0.0", canonical_payload(content)),
        )
        actor = ActorRef(
            actor_id="agent:hypothesis-service",
            kind=ActorKind.AGENT,
            role="hypothesis-editor",
        )
        revision = SemanticRevision(
            revision_id=revision_id,
            project_id=record.project_id,
            entity_type=EntityType.HYPOTHESIS,
            entity_id=entity_id,
            snapshot_id=snapshot.snapshot_id,
            parent_revision_digests=() if supersedes is None else (supersedes,),
            actor=actor,
            reason=event_type,
            evidence_refs=evidence_refs,
            affected_refs=(),
            revision_digest=revision_digest,
            created_at=record.created_at,
        )
        expected = {} if supersedes is None else {f"HYPOTHESIS:{entity_id}": supersedes}
        commit = self._commits.commit(
            RevisionChangeSet(
                changeset_id=self._ids.new("changeset"),
                project_id=record.project_id,
                expected_heads=expected,
                staged_revisions=(StagedRevision(snapshot=snapshot, revision=revision),),
                impact_plan=ImpactPropagationPlan(),
                actor=actor,
                reason=event_type,
            )
        )
        if not commit.committed_revision_ids:
            raise ValueError("hypothesis commit branched because expected revision changed")
        return commit

    def _check_object_head(
        self, project_id: str, object_id: str, expected: str | None
    ) -> DecisionObjectRecord:
        record = self._object(project_id, object_id)
        head = self._ledger.read_heads(project_id).get(f"DECISION_OBJECT:{object_id}")
        if head != record.revision_digest or (expected is not None and head != expected):
            raise ValueError("DECISION_OBJECT_REVISION_CONFLICT")
        return record

    def _object(self, project_id: str, object_id: str) -> DecisionObjectRecord:
        value = self._objects.read_object(project_id, object_id, None)
        if value is None:
            raise ValueError("DecisionObject was not found")
        return value

    def _hypotheses(
        self, project_id: str, hypothesis_ids: Iterable[str]
    ) -> tuple[HypothesisRecord, ...]:
        values: list[HypothesisRecord] = []
        for hypothesis_id in hypothesis_ids:
            value = self._store.read_hypothesis(project_id, hypothesis_id, None)
            if value is None:
                raise ValueError(f"hypothesis not found: {hypothesis_id}")
            values.append(value)
        return tuple(values)

    def _validate_evidence(self, project_id: str, refs: tuple[str, ...]) -> None:
        for reference in refs:
            span = self._artifacts.read_evidence(reference)
            if span is None or span.project_id != project_id:
                raise ValueError("hypothesis evidence ref is outside the project")
            if span.cutoff_state != CutoffState.ELIGIBLE:
                raise ValueError("future or prohibited evidence cannot enter hypothesis context")

    @staticmethod
    def _validate_intents(primary: str, secondary: tuple[str, ...]) -> None:
        if primary not in PRIMARY_INTENTS or any(item not in PRIMARY_INTENTS for item in secondary):
            raise ValueError("unsupported hypothesis intent")

    @staticmethod
    def _digest(value: dict[str, object]) -> str:
        return domain_digest("HYPOTHESIS_RECORD", "2.0.0", canonical_payload(value))

    @staticmethod
    def _portfolio_digest(value: dict[str, object]) -> str:
        return domain_digest("HYPOTHESIS_PORTFOLIO", "2.0.0", canonical_payload(value))

    @staticmethod
    def _relation_digest(value: dict[str, object]) -> str:
        return domain_digest("HYPOTHESIS_RELATION", "1.0.0", canonical_payload(value))

    @staticmethod
    def _aux_digest(kind: str, value: dict[str, object]) -> str:
        return domain_digest(f"HYPOTHESIS_{kind}", "1.0.0", canonical_payload(value))
