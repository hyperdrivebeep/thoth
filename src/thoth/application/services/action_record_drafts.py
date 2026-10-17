"""Build Action, Portfolio and Plan drafts; reads, validation, digests and persistence stay put."""

from __future__ import annotations

from typing import cast

from thoth.application.services.action_effect_policy import effect_impact
from thoth.domain.action_full import (
    ActionPlanRecord,
    ActionPortfolioRecord,
    ActionRecord,
    ActionTestRef,
)
from thoth.ports.runtime import ClockPort, IdGeneratorPort


def creation_expectation(
    specification: dict[str, object], effect: dict[str, object]
) -> tuple[dict[str, object], tuple[str, ...]]:
    expected = specification.get("expected_observation_or_change")
    expected_mapping: dict[str, object] = (
        {str(key): child for key, child in cast(dict[object, object], expected).items()}
        if isinstance(expected, dict)
        else {"description": "UNRESOLVED", "status": "MISSING"}
    )
    missing = tuple(
        field
        for field, present in (
            ("expected_observation_or_change", bool(expected_mapping)),
            (
                "effect_completeness_confirmed",
                effect.get("effect_completeness_confirmed") is True,
            ),
            ("stop_conditions", bool(specification.get("stop_conditions"))),
            ("observability", bool(specification.get("observability"))),
        )
        if not present
    )
    return expected_mapping, missing


def creation_draft(
    *,
    project_id: str,
    object_id: str,
    portfolio_id: str,
    hypothesis_refs: tuple[str, ...],
    test_refs: tuple[ActionTestRef, ...],
    primary_purpose: str,
    secondary_purposes: tuple[str, ...],
    specification: dict[str, object],
    evidence_refs: tuple[str, ...],
    effect: dict[str, object],
    risk_tier: str,
    policy_state: str,
    processes: tuple[str, ...],
    roles: tuple[str, ...],
    expected_mapping: dict[str, object],
    missing: tuple[str, ...],
    ids: IdGeneratorPort,
    clock: ClockPort,
) -> dict[str, object]:
    draft: dict[str, object] = {
        "action_revision_id": ids.new("action-revision"),
        "action_id": ids.new("action"),
        "project_id": project_id,
        "object_id": object_id,
        "portfolio_id": portfolio_id,
        "hypothesis_refs": hypothesis_refs,
        "test_refs": test_refs,
        "primary_purpose": primary_purpose,
        "secondary_purposes": secondary_purposes,
        "specification": {**specification, "missing_fields": missing},
        "evidence_refs": evidence_refs,
        "expected_observation_or_change": expected_mapping,
        "effect_vector": effect,
        "impact_set": effect_impact(effect),
        "risk_tier": risk_tier,
        "required_processes": processes,
        "required_roles": roles,
        "proposal_state": "CANDIDATE" if not missing else "DRAFT",
        "decision_state": "NOT_EVALUATED",
        "policy_state": policy_state,
        "authorization_state": (
            "NOT_REQUIRED" if risk_tier in {"R0", "R1", "R2", "R4"} else "NOT_PREPARED"
        ),
        "freshness": "CURRENT",
        "created_at": clock.now(),
    }
    return draft


def portfolio_composition_draft(
    *,
    project_id: str,
    object_id: str,
    action_ids: tuple[str, ...],
    decision_need: str,
    criteria_proposal: tuple[dict[str, object], ...],
    effective_id: str,
    current: ActionPortfolioRecord | None,
    ids: IdGeneratorPort,
    clock: ClockPort,
) -> dict[str, object]:
    mandatory = tuple(item for item in criteria_proposal if item.get("mandatory") is True)
    enhancing = tuple(item for item in criteria_proposal if item.get("mandatory") is not True)
    if not mandatory:
        mandatory = (
            {
                "criterion_id": "mandatory:policy",
                "name": "policy admissibility",
                "mandatory": True,
                "rationale": "prohibited actions cannot enter the feasible set",
            },
        )
    draft: dict[str, object] = {
        "portfolio_revision_id": ids.new("action-portfolio-revision"),
        "portfolio_id": effective_id,
        "project_id": project_id,
        "object_id": object_id,
        "action_refs": action_ids,
        "decision_need": decision_need,
        "mandatory_criteria": mandatory,
        "enhancing_criteria": enhancing,
        "evaluation_method": "NOT_EVALUATED",
        "scenario_results": (),
        "uncertainty": "UNASSESSED",
        "sensitivity": "UNASSESSED",
        "value_of_information": "UNASSESSED",
        "decision_state": "NOT_EVALUATED",
        "supersedes_revision_digest": None if current is None else current.revision_digest,
        "created_at": clock.now(),
    }
    return draft


def portfolio_evaluation_updates(
    actions: tuple[ActionRecord, ...],
    *,
    evaluation_method: str,
    preference_inputs: tuple[dict[str, object], ...],
) -> dict[str, object]:
    results: list[dict[str, object]] = []
    feasible: list[str] = []
    for action in actions:
        mandatory_pass = action.policy_state != "PROHIBITED"
        if mandatory_pass:
            feasible.append(action.action_id)
        results.append(
            {
                "action_id": action.action_id,
                "mandatory_gate": "PASS" if mandatory_pass else "FAIL",
                "risk_tier": action.risk_tier,
                "impact": action.impact_set,
                "expected": action.expected_observation_or_change,
            }
        )
    preference_required = len(feasible) > 1 and not preference_inputs
    state = (
        "PREFERENCE_REQUIRED"
        if preference_required
        else "RECOMMENDATION_READY"
        if feasible
        else "EVALUATED"
    )
    return {
        "evaluation_method": evaluation_method,
        "scenario_results": tuple(results),
        "uncertainty": "EXPLICIT_SCENARIO_COMPARISON",
        "sensitivity": (
            "RANK_UNSTABLE_WITHOUT_PREFERENCES"
            if preference_required
            else "STABLE_WITH_SUPPLIED_PREFERENCES"
        ),
        "value_of_information": (
            "PREFERENCE_INPUT_HAS_DECISION_VALUE"
            if preference_required
            else "NO_ADDITIONAL_PREFERENCE_REQUIRED"
        ),
        "decision_state": state,
        "recommendation_ref": None,
    }


def portfolio_revision_draft(
    current: ActionPortfolioRecord,
    *,
    updates: dict[str, object],
    ids: IdGeneratorPort,
    clock: ClockPort,
) -> dict[str, object]:
    draft = current.model_dump(mode="python")
    draft.update(updates)
    draft.update(
        {
            "portfolio_revision_id": ids.new("action-portfolio-revision"),
            "supersedes_revision_digest": current.revision_digest,
            "created_at": clock.now(),
        }
    )
    draft.pop("revision_digest", None)
    return draft


def plan_composition_draft(
    *,
    project_id: str,
    object_id: str,
    selected_action_refs: tuple[str, ...],
    steps: tuple[dict[str, object], ...],
    dependency_edges: tuple[dict[str, str], ...],
    effective_id: str,
    derived: dict[str, object],
    current: ActionPlanRecord | None,
    ids: IdGeneratorPort,
    clock: ClockPort,
) -> dict[str, object]:
    draft: dict[str, object] = {
        "plan_revision_id": ids.new("action-plan-revision"),
        "plan_id": effective_id,
        "project_id": project_id,
        "object_id": object_id,
        "selected_action_refs": selected_action_refs,
        "steps": steps,
        "dependency_edges": dependency_edges,
        **derived,
        "authorization_refs": () if current is None else current.authorization_refs,
        "validation_state": "VALIDATED",
        "freshness": "CURRENT",
        "supersedes_revision_digest": None if current is None else current.revision_digest,
        "created_at": clock.now(),
    }
    return draft
