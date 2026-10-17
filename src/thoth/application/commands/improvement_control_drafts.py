"""Pure payload construction and evaluation reductions for Improvement commands.

The handler keeps authority, storage, evidence-currentness checks and transactions. Inputs are
already-read typed records and validated requests; helpers perform no I/O or ID/clock reads.
"""

from __future__ import annotations

from typing import cast

from thoth.application.commands.improvement_inputs import (
    EvaluationAssessInput,
    EvaluationPlanInput,
    ExposurePrepareInput,
    PromotionDecideInput,
    PromotionPrepareInput,
    ProposeInput,
    RetireInput,
    ReviseInput,
    RollbackPrepareInput,
)
from thoth.application.services.improvement_evidence import plan_matches_improvement
from thoth.domain.behavior_execution import BehaviorBaselineRevision
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.control_record import ControlRecord
from thoth.domain.evaluation_run import PairedRunRecord


def proposal_fields(request: ProposeInput, candidate_digest: str) -> dict[str, object]:
    return {
        "thread_id": request.thread_id,
        "target_component": request.target_component,
        "scope_key": request.scope_key,
        "trigger_refs": request.trigger_refs,
        "baseline_digest": request.baseline_digest,
        "candidate_content": cast(dict[str, object], request.candidate_content),
        "candidate_digest": candidate_digest,
        "improvement_hypothesis": request.improvement_hypothesis,
        "evaluation_contract_ref": request.evaluation_contract_ref,
        "candidate_lifecycle": "PROPOSED",
        "evaluation_validity": "NOT_ASSESSED",
        "performance_verdict": "INCONCLUSIVE",
        "exposure_state": "NONE",
        "promotion_state": "NOT_ELIGIBLE",
    }


def revised_payload(current: ControlRecord, request: ReviseInput) -> dict[str, object]:
    payload = {
        **current.payload,
        **cast(dict[str, object], request.patch),
        "evaluation_validity": "NOT_ASSESSED",
        "performance_verdict": "INCONCLUSIVE",
        "promotion_state": "NOT_ELIGIBLE",
        "evidence_refs": request.evidence_refs,
        "revision_reason": request.reason,
    }
    if "candidate_content" in request.patch:
        content = cast(dict[str, object], request.patch["candidate_content"])
        payload["candidate_digest"] = domain_digest(
            "IMPROVEMENT_CANDIDATE", "1.0.0", canonical_payload(content)
        )
    return payload


def evaluation_plan_state(
    request: EvaluationPlanInput, improvement: ControlRecord
) -> tuple[bool, bool, str]:
    hidden_exposure = any(item.get("contains_expected_values") is True for item in request.datasets)
    self_evaluation = improvement.payload.get("target_component") == "EVALUATOR_CODE" and any(
        ref == improvement.payload.get("candidate_digest") for ref in request.evaluator_refs
    )
    state = (
        "HELD"
        if hidden_exposure
        or self_evaluation
        or request.baseline_digest != improvement.payload.get("baseline_digest")
        else "PLANNED"
    )
    return (hidden_exposure, self_evaluation, state)


def evaluation_plan_payload(
    request: EvaluationPlanInput,
    improvement: ControlRecord,
    self_evaluation: bool,
    hidden_exposure: bool,
) -> dict[str, object]:
    return {
        "improvement_revision_id": improvement.record_id,
        "improvement_record_digest": improvement.record_digest,
        "baseline_digest": request.baseline_digest,
        "candidate_digest": improvement.payload.get("candidate_digest"),
        "datasets": request.datasets,
        "evaluator_refs": request.evaluator_refs,
        "guardrails": request.guardrails,
        "exposure_policy_ref": request.exposure_policy_ref,
        "evaluator_isolation": not self_evaluation,
        "hidden_expected_value_access": hidden_exposure,
        "evaluation_revision": 1,
        "results": (),
    }


def assessment_state(
    plan: ControlRecord,
    improvement: ControlRecord | None,
    measured: PairedRunRecord | None,
    critical_failure: bool,
) -> tuple[str, str]:
    pair = None if measured is None else measured.result
    bound = (
        pair is not None and improvement is not None and plan_matches_improvement(plan, improvement)
    )
    if bound and measured is not None:
        bound = (
            measured.spec.plan_id == plan.record_id
            and measured.spec.plan_digest == plan.record_digest
            and (measured.spec.baseline_digest == plan.payload.get("baseline_digest"))
            and (measured.spec.candidate_digest == plan.payload.get("candidate_digest"))
        )
    validity = (
        "INVALID"
        if plan.state == "HELD"
        else pair.validity
        if bound and pair is not None
        else "INCONCLUSIVE"
    )
    verdict = (
        "INCONCLUSIVE"
        if validity != "VALID"
        else "WORSE"
        if critical_failure
        else pair.performance_verdict
        if pair is not None
        else "INCONCLUSIVE"
    )
    return (validity, verdict)


def assessment_payload(
    plan: ControlRecord,
    request: EvaluationAssessInput,
    validity: str,
    verdict: str,
    guardrails: tuple[dict[str, object], ...],
    critical_failure: bool,
    eligible: bool,
) -> dict[str, object]:
    return {
        **plan.payload,
        "evaluation_revision": plan.version + 1,
        "result_refs": request.result_refs,
        "evaluator_result_refs": request.evaluator_result_refs,
        "exposure_ledger_ref": request.exposure_ledger_ref,
        "evaluation_validity": validity,
        "performance_verdict": verdict,
        "guardrail_vector": guardrails,
        "critical_guardrail_failure": critical_failure,
        "promotion_eligible": eligible,
    }


def exposure_payload(
    request: ExposurePrepareInput,
    improvement: ControlRecord,
    evaluation: ControlRecord,
    requires_authority: bool,
) -> dict[str, object]:
    return {
        "improvement_revision_id": improvement.record_id,
        "evaluation_plan_id": evaluation.record_id,
        "requested_exposure_state": request.requested_exposure_state,
        "scope": request.scope,
        "duration_budget": request.duration_budget,
        "stop_rollback_contract": request.stop_rollback_contract,
        "output_authoritative": False,
        "policy_requirements": "PROTECTED_R3" if requires_authority else "R0_R2_BOUNDED",
        "authorization_required": requires_authority,
    }


def promotion_payload(
    request: PromotionPrepareInput,
    improvement: ControlRecord,
    assessments: tuple[ControlRecord | None, ...],
    runtime_payload: dict[str, object],
) -> dict[str, object]:
    return {
        "improvement_revision_id": improvement.record_id,
        "improvement_record_digest": improvement.record_digest,
        "assessment_refs": request.assessment_refs,
        "assessment_digests": {
            item.record_id: item.record_digest for item in assessments if item is not None
        },
        "candidate_digest": request.candidate_digest,
        "baseline_digest": request.baseline_digest,
        "target_scope": request.target_scope,
        "policy_version": request.policy_version,
        "required_roles": ("improvement-owner",),
        **runtime_payload,
        "exact_impact": {
            "target_component": improvement.payload.get("target_component"),
            "scope_key": improvement.payload.get("scope_key"),
        },
    }


def promotion_decision_payload(
    promotion: ControlRecord, request: PromotionDecideInput
) -> dict[str, object]:
    return {
        **promotion.payload,
        "decision": request.decision,
        "actor_ref": request.actor_ref,
        "role_assignment_ref": request.role_assignment_ref,
        "reason": request.reason,
    }


def baseline_change_payload(
    decided: ControlRecord, request: PromotionDecideInput, applied: BehaviorBaselineRevision | None
) -> dict[str, object]:
    return {
        "promotion_id": decided.record_id,
        "baseline_change": request.decision == "APPROVE",
        "current_state_mutated": applied is not None,
        "baseline_kind": None if applied is None else "BEHAVIOR",
        "behavior_baseline_receipt": None if applied is None else applied.receipt_digest,
    }


def rollback_payload(request: RollbackPrepareInput) -> dict[str, object]:
    return {
        "promoted_revision_ref": request.promoted_revision_ref,
        "guardrail_failure_refs": request.guardrail_failure_refs,
        "rollback_target_digest": request.rollback_target_digest,
        "target_scope": request.target_scope,
        "authority": "PREAUTHORIZED" if request.guardrail_failure_refs else "PROTECTED_R3",
        "exact_rollback_claimed": False,
        "residual_state": "REQUIRES_REASSESSMENT",
        "current_state_mutated": False,
    }


def retirement_payload(current: ControlRecord, request: RetireInput) -> dict[str, object]:
    return {
        "improvement_revision_id": current.record_id,
        "reason": request.reason,
        "evidence_refs": request.evidence_refs,
        "exposure_cleanup": "REQUIRED",
        "retention_impact": "PRESERVE_EVALUATION_HISTORY",
    }
