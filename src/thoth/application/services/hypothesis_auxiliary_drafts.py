"""Auxiliary draft construction with ordered Clock/ID reads and no persistence."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from thoth.domain.hypothesis_full import (
    HypothesisPortfolioRecord,
    HypothesisRecord,
    HypothesisRelationRecord,
    PredictionRecord,
)
from thoth.domain.test_validity import ResearchMeasurementContract, TestValidityAssessment
from thoth.ports.runtime import ClockPort, IdGeneratorPort


class PredictionDraftBasis(Protocol):
    @property
    def contract(self) -> ResearchMeasurementContract: ...

    @property
    def contract_digest(self) -> str: ...

    @property
    def semantic_digest(self) -> str: ...


def relation_draft(
    current: HypothesisPortfolioRecord,
    *,
    source_hypothesis_id: str,
    relation_type: str,
    target_hypothesis_id: str,
    evidence_refs: tuple[str, ...],
    semantic_role: str | None,
    ids: IdGeneratorPort,
    clock: ClockPort,
) -> dict[str, object]:
    draft: dict[str, object] = {
        "relation_revision_id": ids.new("hypothesis-relation-revision"),
        "relation_id": ids.new("hypothesis-relation"),
        "project_id": current.project_id,
        "portfolio_id": current.portfolio_id,
        "source_hypothesis_id": source_hypothesis_id,
        "relation_type": relation_type,
        "target_hypothesis_id": target_hypothesis_id,
        "evidence_refs": evidence_refs,
        "semantic_role": semantic_role,
        "authority_state": "UNCLASSIFIED",
        "active": True,
        "created_at": clock.now(),
    }
    return draft


def ended_relation_draft(
    relation: HypothesisRelationRecord,
    *,
    ids: IdGeneratorPort,
    clock: ClockPort,
) -> dict[str, object]:
    draft = relation.model_dump(mode="python")
    draft.update(
        {
            "relation_revision_id": ids.new("hypothesis-relation-revision"),
            "active": False,
            "supersedes_revision_digest": relation.revision_digest,
            "created_at": clock.now(),
        }
    )
    draft.pop("revision_digest", None)
    return draft


def assumption_draft(
    current: HypothesisRecord,
    *,
    statement: str,
    role: str,
    evidence_refs: tuple[str, ...],
    validation_route: str | None,
    ids: IdGeneratorPort,
    clock: ClockPort,
) -> dict[str, object]:
    draft: dict[str, object] = {
        "assumption_id": ids.new("hypothesis-assumption"),
        "project_id": current.project_id,
        "hypothesis_id": current.hypothesis_id,
        "statement": statement,
        "role": role,
        "evidence_refs": evidence_refs,
        "validation_route": validation_route,
        "status": "UNVALIDATED",
        "testability": "TESTABLE" if validation_route else "NOT_ASSESSED",
        "created_at": clock.now(),
    }
    return draft


def prediction_draft(
    current: HypothesisRecord,
    *,
    knowledge_cutoff: datetime,
    prespecification_state: str,
    conditions: dict[str, str],
    measurement_contract_ref: str,
    assumption_refs: tuple[str, ...],
    expected_outcome: dict[str, object],
    discrimination_map: dict[str, object],
    basis: PredictionDraftBasis,
    ids: IdGeneratorPort,
    clock: ClockPort,
) -> dict[str, object]:
    draft: dict[str, object] = {
        "prediction_id": ids.new("prediction"),
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
        "created_at": clock.now(),
        "object_id": current.object_id,
        "measurement_contract": basis.contract,
        "measurement_contract_digest": basis.contract_digest,
        "hypothesis_semantic_digest": basis.semantic_digest,
        "unbound_assumption_candidates": ()
        if current.generation_details is None
        else current.generation_details.assumptions,
        "schema_version": "1.1.0",
    }
    return draft


def test_binding_draft(
    prediction: PredictionRecord,
    *,
    execution_ref: str,
    observation_refs: tuple[str, ...],
    test_validity_assessment_ref: str,
    test_validity: str,
    prediction_fit: str,
    assessment: TestValidityAssessment,
    ids: IdGeneratorPort,
    clock: ClockPort,
) -> dict[str, object]:
    draft: dict[str, object] = {
        "test_binding_id": ids.new("hypothesis-test-binding"),
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
        "created_at": clock.now(),
    }
    return draft


def appraisal_draft(
    current: HypothesisRecord,
    *,
    evidence_refs: tuple[str, ...],
    test_assessment_refs: tuple[str, ...],
    appraisal_scope: dict[str, str],
    appraisal: str,
    rationale: str,
    valid: bool,
    ids: IdGeneratorPort,
    clock: ClockPort,
) -> dict[str, object]:
    draft: dict[str, object] = {
        "appraisal_id": ids.new("hypothesis-appraisal"),
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
        "created_at": clock.now(),
    }
    return draft
