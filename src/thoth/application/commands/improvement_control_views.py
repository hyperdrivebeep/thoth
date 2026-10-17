"""Pure response dictionaries for Improvement commands.

Preserve the public response fields and the payloads used by notification consumers; decisions,
writes and scope checks stay with ImprovementHandlers.
"""

from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from thoth.domain.behavior_execution import BehaviorBaselineRevision, BehaviorExposureRecord
from thoth.domain.control_record import ControlRecord


def proposal_response(
    record: ControlRecord, active: tuple[ControlRecord, ...]
) -> dict[str, JsonValue]:
    return {
        "improvement": record.model_dump(mode="json"),
        "overlap_lock_check": not active,
        "prohibited_target_check": True,
    }


def revision_response(revised: ControlRecord) -> dict[str, JsonValue]:
    return {"improvement": revised.model_dump(mode="json"), "old_evaluation_invalidated": True}


def evaluation_plan_response(
    plan: ControlRecord, self_evaluation: bool, hidden_exposure: bool
) -> dict[str, JsonValue]:
    return {
        "evaluation_plan": plan.model_dump(mode="json"),
        "evaluator_isolation": not self_evaluation,
        "holdout_access_check": not hidden_exposure,
        "action_plan_candidates": [],
    }


def assessment_response(
    assessed: ControlRecord,
    validity: str,
    verdict: str,
    guardrails: tuple[dict[str, object], ...],
    eligible: bool,
) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        {
            "evaluation": assessed.model_dump(mode="json"),
            "evaluation_validity": validity,
            "performance_verdict": verdict,
            "guardrail_vector": list(guardrails),
            "promotion_eligibility": eligible,
        },
    )


def runtime_exposure_response(
    projection: ControlRecord, runtime: BehaviorExposureRecord
) -> dict[str, JsonValue]:
    return {
        "exposure": projection.model_dump(mode="json"),
        "runtime_exposure": runtime.model_dump(mode="json"),
        "approval_target_digest": runtime.spec.spec_digest,
        "policy_authorization_requirements": "PROTECTED_R3"
        if runtime.spec.stage == "CANARY"
        else "R0_R2_BOUNDED",
        "actual_execution_observed": False,
    }


def exposure_response(exposure: ControlRecord) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        {
            "exposure": exposure.model_dump(mode="json"),
            "action_plan_candidate": {"owner": "ACTION", "automatic_execution": False},
            "policy_authorization_requirements": exposure.payload["policy_requirements"],
        },
    )


def promotion_response(promotion: ControlRecord, state: str) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        {
            "promotion": promotion.model_dump(mode="json"),
            "eligibility": state,
            "exact_impact": promotion.payload["exact_impact"],
        },
    )


def promotion_decision_response(
    decided: ControlRecord, change_set: ControlRecord, applied: BehaviorBaselineRevision | None
) -> dict[str, JsonValue]:
    return {
        "promotion": decided.model_dump(mode="json"),
        "revision_baseline_change_set_proposal": change_set.model_dump(mode="json"),
        "current_state_mutated": applied is not None,
        "behavior_baseline_revision": None if applied is None else applied.model_dump(mode="json"),
    }


def rollback_response(rollback: ControlRecord) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        {
            "rollback": rollback.model_dump(mode="json"),
            "action_revision_change_set": {"automatic_execution": False},
            "residual_state": rollback.payload["residual_state"],
        },
    )
