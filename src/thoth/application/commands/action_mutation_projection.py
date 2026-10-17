"""Pure response assembly of the Action mutation handlers.

Each function builds the JSON-RPC result of one route from values the handler already holds. The
arguments carry the names of the handler's own locals, and nothing here reads or writes storage,
calls the service, checks a guard or converts an error; those stay in ActionHandlers.
"""

from __future__ import annotations

from collections.abc import Sized
from typing import cast

from pydantic import JsonValue

from thoth.application.commands.action_inputs import (
    AuthorizationPrepareInput,
    CompensationCreateInput,
    ImpactRecalculateInput,
    MergeProposeInput,
    PlanRevalidateInput,
    PolicyClassifyInput,
    SelectInput,
    StepReviseInput,
)
from thoth.application.commands.action_read_projection import plan_step
from thoth.application.services.revision_service import CommitResult
from thoth.domain.action_full import (
    ActionPlanRecord,
    ActionPortfolioRecord,
    ActionRecord,
    AuthorizationEnvelopeRecord,
)
from thoth.domain.canonical import canonical_payload, domain_digest


def create_result(
    item: ActionRecord, duplicates: tuple[str, ...], commit: CommitResult
) -> dict[str, JsonValue]:
    missing = item.specification.get("missing_fields", ())
    return {
        "action": item.model_dump(mode="json"),
        "impact_policy_precheck": {
            "risk_tier": item.risk_tier,
            "policy_state": item.policy_state,
        },
        "missing_fields": (
            [str(item) for item in cast(tuple[object, ...], missing)]
            if isinstance(missing, tuple)
            else []
        ),
        "duplicate_candidates": list(duplicates),
        "commit": commit.model_dump(mode="json"),
    }


def portfolio_compose_result(
    portfolio: ActionPortfolioRecord, commit: CommitResult
) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        {
            "portfolio": portfolio.model_dump(mode="json"),
            "missing_alternatives": [],
            "missing_criteria": [],
            "commit": commit.model_dump(mode="json"),
        },
    )


def portfolio_evaluate_result(
    revised: ActionPortfolioRecord, commit: CommitResult
) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        {
            "portfolio": revised.model_dump(mode="json"),
            "mandatory_gate_results": list(revised.scenario_results),
            "comparison": list(revised.scenario_results),
            "uncertainty": revised.uncertainty,
            "sensitivity": revised.sensitivity,
            "value_of_information": revised.value_of_information,
            "rank_stability": revised.decision_state,
            "commit": commit.model_dump(mode="json"),
        },
    )


def recommend_result(revised: ActionPortfolioRecord, commit: CommitResult) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        {
            "portfolio": revised.model_dump(mode="json"),
            "recommendation": revised.recommendation_ref,
            "selection_implied": False,
            "authorization_implied": False,
            "sensitivity": revised.sensitivity,
            "commit": commit.model_dump(mode="json"),
        },
    )


def select_result(
    request: SelectInput,
    action: ActionRecord,
    portfolio: ActionPortfolioRecord,
    action_commit: CommitResult,
    portfolio_commit: CommitResult,
) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        {
            "action": action.model_dump(mode="json"),
            "portfolio": portfolio.model_dump(mode="json"),
            "selection_rationale": request.decision_context,
            "authorization_implied": False,
            "commits": [
                action_commit.model_dump(mode="json"),
                portfolio_commit.model_dump(mode="json"),
            ],
        },
    )


def plan_compose_result(plan: ActionPlanRecord, commit: CommitResult) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        {
            "plan": plan.model_dump(mode="json"),
            "profile_schema_impact_policy_validation": plan.validation_state,
            "initial_frontier": list(plan.auto_executable_frontier),
            "commit": commit.model_dump(mode="json"),
        },
    )


def plan_revalidate_result(
    revised: ActionPlanRecord, request: PlanRevalidateInput, commit: CommitResult
) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        {
            "plan": revised.model_dump(mode="json"),
            "dependency": list(revised.dependency_edges),
            "impact": revised.cumulative_impact,
            "policy": list(revised.required_process_union),
            "frontier": list(revised.auto_executable_frontier),
            "point_of_no_return": list(revised.point_of_no_return_steps),
            "authorization": list(revised.authorization_refs),
            "trigger_reason": request.trigger_reason,
            "commit": commit.model_dump(mode="json"),
        },
    )


def step_add_result(
    revised: ActionPlanRecord, step_id: str, commit: CommitResult
) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        {
            "plan": revised.model_dump(mode="json"),
            "step": plan_step(revised, step_id),
            "frontier_delta": list(revised.auto_executable_frontier),
            "commit": commit.model_dump(mode="json"),
        },
    )


def step_revise_result(
    revised: ActionPlanRecord, request: StepReviseInput, commit: CommitResult
) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        {
            "plan": revised.model_dump(mode="json"),
            "step": plan_step(revised, request.step_id),
            "invalidated_authorizations_executions": "ALL_BOUND_TO_PREVIOUS_PLAN_REVISION",
            "cumulative_impact": revised.cumulative_impact,
            "commit": commit.model_dump(mode="json"),
        },
    )


def impact_recalculate_result(
    revised: ActionPlanRecord, request: ImpactRecalculateInput, commit: CommitResult
) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        {
            "plan": revised.model_dump(mode="json"),
            "per_step_effects": revised.cumulative_impact.get("per_step", {}),
            "cumulative_effects": revised.cumulative_impact,
            "process_union": list(revised.required_process_union),
            "role_union": list(revised.required_role_union),
            "risk_changes": [],
            "trigger_refs": list(request.trigger_refs),
            "commit": commit.model_dump(mode="json"),
        },
    )


def policy_classify_result(
    revised: ActionPlanRecord, request: PolicyClassifyInput, commit: CommitResult
) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        {
            "plan": revised.model_dump(mode="json"),
            "risk_tiers": {str(step["step_id"]): step.get("risk_tier") for step in revised.steps},
            "policy_states": {
                str(step["step_id"]): step.get("policy_state") for step in revised.steps
            },
            "required_processes": list(revised.required_process_union),
            "required_roles": list(revised.required_role_union),
            "unknown_mappings": [
                str(step["step_id"])
                for step in revised.steps
                if step.get("policy_state") == "POLICY_UNDEFINED"
            ],
            "policy_version": request.policy_version or "project-policy:current",
            "commit": commit.model_dump(mode="json"),
        },
    )


def authorization_prepare_result(
    envelope: AuthorizationEnvelopeRecord | None, state: str, request: AuthorizationPrepareInput
) -> dict[str, JsonValue]:
    return {
        "authorization": None if envelope is None else envelope.model_dump(mode="json"),
        "state": state,
        "preflight_requirements": {
            "predecessor_outputs": list(request.predecessor_output_digests),
            "target_baselines": list(request.target_baseline_digests),
        },
    }


def authorization_decide_result(decided: AuthorizationEnvelopeRecord) -> dict[str, JsonValue]:
    return {
        "authorization": decided.model_dump(mode="json"),
        "exact_scope_digest": decided.exact_scope_digest,
        "single_use": decided.single_use,
    }


def compensation_specification(request: CompensationCreateInput) -> dict[str, object]:
    return {
        "description": request.intended_mitigation,
        "caused_by_execution_attempt_ref": request.caused_by_execution_attempt_ref,
        "observed_effect_refs": request.observed_effect_refs,
        "expected_observation_or_change": {
            "residual_effect_expectation": request.residual_effect_expectation
        },
        "effect_completeness_confirmed": False,
        "effect_vector": {"effect_completeness_confirmed": False},
        "stop_conditions": ("mitigation boundary reached",),
        "observability": "residual effect evidence required",
    }


def compensation_result(
    request: CompensationCreateInput, action: ActionRecord, commit: CommitResult
) -> dict[str, JsonValue]:
    return {
        "action": action.model_dump(mode="json"),
        "exact_rollback_claimed": False,
        "residual_risk_contract": request.residual_effect_expectation,
        "commit": commit.model_dump(mode="json"),
    }


def merge_proposal_payload(
    request: MergeProposeInput, purposes: Sized
) -> tuple[dict[str, JsonValue], str]:
    payload: dict[str, JsonValue] = {
        "project_id": request.project_id,
        "action_ids": list(request.action_ids),
        "field_mapping": request.field_mapping,
        "evidence_refs": list(request.evidence_refs),
        "rationale": request.rationale,
        "expected_revision_digests": list(request.expected_revision_digests),
        "conflicts": cast(JsonValue, [] if len(purposes) == 1 else ["primary_purpose"]),
        "applied": False,
        "owner_namespace": "REVISION",
    }
    digest = domain_digest("ACTION_MERGE_PROPOSAL", "1.0.0", canonical_payload(payload))
    return payload, digest


def merge_proposal_result(
    payload: dict[str, JsonValue],
    digest: str,
    actions: tuple[ActionRecord | None, ...],
    request: MergeProposeInput,
) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        {
            "merge_proposal": {**payload, "digest": digest},
            "impact_preview": [action.impact_set for action in actions if action is not None],
            "lineage_preview": [f"SUPERSEDES:{item}" for item in request.action_ids],
        },
    )
