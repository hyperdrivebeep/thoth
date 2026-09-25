from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from thoth.domain.action_full import ActionRecord, AuthorizationEnvelopeRecord
from thoth.domain.decision_object_full import DecisionObjectRecord
from thoth.domain.execution_full import StepExecutionAttemptRecord
from thoth.domain.hypothesis_full import HypothesisTestBinding
from thoth.domain.outcome_full import OutcomeAssessmentRecord
from thoth.domain.receipt import Receipt

NOW = datetime(2026, 8, 31, tzinfo=UTC)
SHA = "a" * 64


def test_r4_policy_mutation_is_rejected() -> None:
    with pytest.raises(ValidationError, match="R4 Action must remain prohibited"):
        ActionRecord(
            action_revision_id="revision:action",
            action_id="action:1",
            project_id="project:1",
            object_id="object:1",
            portfolio_id="portfolio:1",
            primary_purpose="GOVERNANCE_ESCALATION",
            specification={},
            evidence_refs=(),
            expected_observation_or_change={},
            effect_vector={"changes_official_kpi": True},
            impact_set={},
            risk_tier="R4",
            required_processes=(),
            required_roles=("institution-authority",),
            policy_state="AUTO_ALLOWED",
            revision_digest=SHA,
            created_at=NOW,
        )


def test_consumed_authorization_without_timestamp_is_rejected() -> None:
    with pytest.raises(ValidationError, match="consumed authorization requires"):
        AuthorizationEnvelopeRecord(
            authorization_revision_id="revision:auth",
            authorization_id="authorization:1",
            project_id="project:1",
            plan_id="plan:1",
            step_id="step:1",
            plan_revision_digest=SHA,
            predecessor_output_digests=(),
            target_baseline_digests=(SHA,),
            policy_version="policy:1",
            exact_scope_digest=SHA,
            required_roles=("project-owner",),
            state="CONSUMED",
            expires_at=NOW,
            revision_digest=SHA,
            created_at=NOW,
        )


def test_invalid_test_cannot_mutate_appraisal() -> None:
    with pytest.raises(ValidationError, match="invalid test cannot mutate"):
        HypothesisTestBinding(
            test_binding_id="binding:1",
            project_id="project:1",
            prediction_id="prediction:1",
            execution_ref="execution:1",
            observation_refs=("span:1",),
            test_validity_assessment_ref="validity:1",
            test_validity="INVALID",
            prediction_fit="MISMATCH",
            appraisal_mutation_allowed=True,
            binding_digest=SHA,
            created_at=NOW,
        )


def test_invalid_outcome_cannot_claim_achievement() -> None:
    with pytest.raises(ValidationError, match="invalid Outcome cannot claim"):
        OutcomeAssessmentRecord(
            assessment_revision_id="revision:outcome",
            outcome_assessment_id="outcome:1",
            project_id="project:1",
            outcome_series_id="series:1",
            object_id="object:1",
            assessment_phase="INTERIM",
            plan_revision_digest=SHA,
            baseline_set_digest=SHA,
            profile_ref="profile:1",
            profile_version=1,
            actual_observation_refs=("span:1",),
            comparator_refs=("span:2",),
            assumptions=(),
            validity="INVALID",
            objective_attainment="ACHIEVED",
            revision_digest=SHA,
            created_at=NOW,
        )


def test_closed_object_without_resolution_is_rejected() -> None:
    with pytest.raises(ValidationError, match="closed object requires"):
        DecisionObjectRecord(
            object_revision_id="revision:object",
            object_id="object:1",
            project_id="project:1",
            thread_id="thread:1",
            purpose_statement="test",
            problem_frame="test",
            focus_refs=("focus:1",),
            materialization_trigger="EXPLICIT_USER_REQUEST",
            trigger_evidence_refs=(),
            entry_criteria={"status": "PASS"},
            exit_criteria={"status": "NOT_SATISFIED"},
            lifecycle="CLOSED",
            resolution="OPEN",
            actor_or_agent_ref="human:1",
            revision_digest=SHA,
            created_at=NOW,
        )


def test_receipt_semantic_truth_claim_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Receipt.model_validate(
            {
                "receipt_id": "receipt:1",
                "project_id": "project:1",
                "receipt_type": "TRANSITION",
                "claim_scopes": ["TRANSITION_RECORDED"],
                "subject_refs": ["revision:1"],
                "before_head_set_digest": SHA,
                "after_head_set_digest": SHA,
                "parent_receipt_refs": [],
                "policy_version": "policy:1",
                "model_versions": [],
                "tool_versions": [],
                "integrity_state": "VALID",
                "provenance_state": "COMPLETE",
                "signature_state": "NOT_PRESENT",
                "timestamp_trust": "LOCAL_ONLY",
                "recorded_at": NOW,
                "receipt_digest": SHA,
                "semantic_truth_certified": True,
            }
        )


def test_successful_attempt_still_keeps_observation_axis_independent() -> None:
    attempt = StepExecutionAttemptRecord(
        attempt_revision_id="revision:attempt",
        attempt_id="attempt:1",
        project_id="project:1",
        plan_execution_id="execution:1",
        plan_id="plan:1",
        plan_revision_digest=SHA,
        step_id="step:1",
        attempt_number=1,
        state="SUCCEEDED",
        exact_invocation_digest=SHA,
        input_digests=(),
        target_digests=(),
        environment_digest=SHA,
        idempotency_key="execution:1:step:1:1",
        delivery_guarantee="IDEMPOTENT_RETRYABLE",
        observation_completeness="MISSING",
        revision_digest=SHA,
        created_at=NOW,
    )
    assert attempt.state == "SUCCEEDED"
    assert attempt.observation_completeness == "MISSING"
