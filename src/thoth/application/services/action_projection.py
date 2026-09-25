"""Typed full Action ownership retains compiled candidates without granting authority."""

from __future__ import annotations

from datetime import datetime

from pydantic import ValidationError

from thoth.domain.action import ActionCandidate, ActionPlan
from thoth.domain.action_full import ActionPlanRecord, ActionPortfolioRecord, ActionRecord
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import RiskTier
from thoth.domain.research_identity import ResearchIdentityError
from thoth.domain.research_projection import ActionGenerationDetails, ActionPlanGenerationDetails


def action_view(record: ActionRecord) -> ActionCandidate | None:
    details = record.generation_details
    text = record.specification.get("description")
    information = record.expected_observation_or_change.get("information_value")
    if details is None or not isinstance(text, str) or not isinstance(information, str):
        return None
    try:
        return ActionCandidate.model_validate(
            {
                "action_id": record.action_id,
                "object_id": record.object_id,
                "hypothesis_ids": record.hypothesis_refs,
                "action_family": details.action_family,
                "specification": text,
                "expected_information_value": information,
                "estimated_cost": details.estimated_cost,
                "estimated_seconds": details.estimated_seconds,
                "risk_tier": record.risk_tier,
                "execution_authority": details.execution_authority,
                "reversibility": details.reversibility,
                "external_write": record.effect_vector.get("external_write"),
                "sandbox_required": details.sandbox_required,
                "state": details.candidate_state,
                "required_approver_role": details.required_approver_role,
                "source_refs": record.evidence_refs,
                "missing_evidence": details.missing_evidence,
                "primary_purpose": record.primary_purpose,
                "effect_facts": details.effect_facts,
                "effect_completeness_confirmed": details.effect_completeness_confirmed,
            }
        )
    except ValidationError:
        return None  # Caller reports an explicit unrepresented/recompilation-required reference.


def action_plan_view(
    record: ActionPlanRecord,
    actions: tuple[ActionRecord, ...],
) -> tuple[ActionPlan | None, tuple[str, ...]]:
    details = record.generation_details
    if details is None:
        return None, (record.plan_id,)
    by_id = {item.action_id: item for item in actions}
    views: list[ActionCandidate] = []
    missing: list[str] = []
    for identifier in details.alternative_refs:
        action = by_id.get(identifier)
        view = (
            None if action is None or action.object_id != record.object_id else action_view(action)
        )
        if view is None:
            missing.append(identifier)
        else:
            views.append(view)
    if missing or len(views) < 2 or len({item.action_family for item in views}) < 2:
        return None, tuple((*missing, record.plan_id))
    steps = {
        str(step["step_id"]): str(step["action_id"]) for step in record.steps if "action_id" in step
    }
    return ActionPlan(
        plan_id=record.plan_id,
        object_id=record.object_id,
        alternatives=tuple(views),
        decision_analysis=details.decision_analysis,
        frontier=tuple(
            steps.get(identifier, identifier) for identifier in record.auto_executable_frontier
        ),
        plan_revision_digest=details.generated_from_head_set,
        next_action_order=details.next_action_order,
        last_r2_execution=details.last_r2_execution,
        recovery_candidate=details.recovery_candidate,
    ), ()


def full_action(
    candidate: ActionCandidate,
    *,
    project_id: str,
    portfolio_id: str,
    revision_id: str,
    created_at: datetime,
    parent: str | None,
    current: ActionRecord | None = None,
) -> ActionRecord:
    ranks = {value.value: index for index, value in enumerate(RiskTier)}
    if current is not None:
        if current.risk_tier not in ranks:
            raise ResearchIdentityError("RESEARCH_ACTION_RISK_SCHEMA_UNRESOLVED")
        if ranks[current.risk_tier] > ranks[candidate.risk_tier.value]:
            raise ResearchIdentityError("RESEARCH_ACTION_RISK_DOWNGRADE_REQUIRES_REVALIDATION")
        declared: set[str] = (
            {candidate.required_approver_role} if candidate.required_approver_role else set()
        )
        if not set(current.required_roles).issubset(declared):
            raise ResearchIdentityError("RESEARCH_ACTION_AUTHORITY_REQUIRES_REVALIDATION")
    purpose: str | None = candidate.primary_purpose
    if purpose is None and current is not None:
        purpose = current.primary_purpose
    draft: dict[str, object] = {} if current is None else current.model_dump(mode="python")
    roles = tuple(
        dict.fromkeys(
            (
                *(current.required_roles if current else ()),
                *((candidate.required_approver_role,) if candidate.required_approver_role else ()),
            )
        )
    )
    draft.update(
        {
            "action_revision_id": revision_id,
            "action_id": candidate.action_id,
            "project_id": project_id,
            "object_id": candidate.object_id,
            "portfolio_id": portfolio_id,
            "hypothesis_refs": candidate.hypothesis_ids,
            "primary_purpose": purpose,
            "specification": {
                **({} if current is None else current.specification),
                "description": candidate.specification,
            },
            "evidence_refs": candidate.source_refs,
            "expected_observation_or_change": {
                **({} if current is None else current.expected_observation_or_change),
                "information_value": candidate.expected_information_value,
            },
            "effect_vector": {
                **({} if current is None else current.effect_vector),
                **(
                    {}
                    if candidate.effect_facts is None
                    else candidate.effect_facts.model_dump(mode="python")
                ),
                "effect_completeness_confirmed": candidate.effect_completeness_confirmed,
                "external_write": candidate.external_write,
                "sandbox_required": candidate.sandbox_required,
            },
            "impact_set": {"object_id": candidate.object_id, "assessment": "CANDIDATE_ONLY"}
            if current is None
            else current.impact_set,
            "risk_tier": candidate.risk_tier.value,
            "required_roles": roles,
            "required_processes": tuple(
                dict.fromkeys(
                    (
                        *(current.required_processes if current else ()),
                        *(("AUTHORIZATION",) if candidate.risk_tier == RiskTier.R3 else ()),
                    )
                )
            ),
            "proposal_state": "DRAFT" if purpose is None else "CANDIDATE",
            "decision_state": "NOT_EVALUATED",
            "policy_state": "PROHIBITED"
            if candidate.risk_tier == RiskTier.R4
            else "COMPILED_CANDIDATE",
            "authorization_state": "REQUIRED"
            if candidate.risk_tier == RiskTier.R3
            else "NOT_REQUIRED",
            "generation_details": ActionGenerationDetails(
                action_family=candidate.action_family,
                estimated_cost=candidate.estimated_cost,
                estimated_seconds=candidate.estimated_seconds,
                execution_authority=candidate.execution_authority,
                reversibility=candidate.reversibility,
                sandbox_required=candidate.sandbox_required,
                candidate_state=candidate.state,
                required_approver_role=candidate.required_approver_role,
                missing_evidence=candidate.missing_evidence,
                effect_facts=candidate.effect_facts,
                effect_completeness_confirmed=candidate.effect_completeness_confirmed,
            ),
            "supersedes_revision_digest": parent,
            "created_at": created_at,
            "receipt_ref": None,
            "schema_version": "1.1.0",
            "revision_digest": "0" * 64,
        }
    )
    record = ActionRecord.model_validate(draft)
    digest = domain_digest(
        "ACTION_RECORD",
        "2.0.0",
        canonical_payload(record.model_dump(mode="python", exclude={"revision_digest"})),
    )
    return record.model_copy(update={"revision_digest": digest})


def full_action_portfolio(
    candidate: ActionPlan,
    *,
    project_id: str,
    portfolio_id: str,
    revision_id: str,
    created_at: datetime,
    parent: str | None,
    retained_refs: tuple[str, ...],
    current: ActionPortfolioRecord | None = None,
) -> ActionPortfolioRecord:
    draft: dict[str, object] = {} if current is None else current.model_dump(mode="python")
    draft.update(
        {
            "portfolio_revision_id": revision_id,
            "portfolio_id": portfolio_id,
            "project_id": project_id,
            "object_id": candidate.object_id,
            "action_refs": tuple(
                dict.fromkeys(
                    (*retained_refs, *(item.action_id for item in candidate.alternatives))
                )
            ),
            "decision_need": candidate.decision_analysis.decision,
            "mandatory_criteria": tuple(
                item.model_dump(mode="python")
                for item in candidate.decision_analysis.criteria
                if item.mandatory
            ),
            "enhancing_criteria": tuple(
                item.model_dump(mode="python")
                for item in candidate.decision_analysis.criteria
                if not item.mandatory
            ),
            "scenario_results": tuple(
                item.model_dump(mode="python") for item in candidate.decision_analysis.evaluations
            ),
            "uncertainty": candidate.decision_analysis.uncertainty,
            "sensitivity": candidate.decision_analysis.sensitivity,
            "decision_state": "NOT_EVALUATED",
            "recommendation_ref": None,
            "selected_action_ref": None,
            "supersedes_revision_digest": parent,
            "created_at": created_at,
            "schema_version": "1.1.0",
            "revision_digest": "0" * 64,
        }
    )
    record = ActionPortfolioRecord.model_validate(draft)
    digest = domain_digest(
        "ACTION_PORTFOLIO",
        "2.0.0",
        canonical_payload(record.model_dump(mode="python", exclude={"revision_digest"})),
    )
    return record.model_copy(update={"revision_digest": digest})


def full_action_plan(
    candidate: ActionPlan,
    *,
    project_id: str,
    portfolio_id: str,
    revision_id: str,
    created_at: datetime,
    parent: str | None,
    actions: tuple[ActionRecord, ...],
    current: ActionPlanRecord | None = None,
) -> ActionPlanRecord:
    draft: dict[str, object] = {} if current is None else current.model_dump(mode="python")
    selected = tuple(item for item in actions if item.action_id in candidate.frontier)
    draft.update(
        {
            "plan_revision_id": revision_id,
            "plan_id": candidate.plan_id,
            "project_id": project_id,
            "object_id": candidate.object_id,
            "selected_action_refs": candidate.frontier,
            "steps": tuple(
                {"step_id": f"step:{item.action_id}", "action_id": item.action_id}
                for item in candidate.alternatives
                if item.action_id in candidate.frontier
            ),
            "dependency_edges": (),
            "cumulative_impact": {"assessment": "CANDIDATE_ONLY"},
            "required_process_union": tuple(
                sorted({value for item in selected for value in item.required_processes})
            ),
            "required_role_union": tuple(
                sorted({value for item in selected for value in item.required_roles})
            ),
            "auto_executable_frontier": candidate.frontier,
            "point_of_no_return_steps": (),
            "authorization_refs": (),
            "validation_state": "COMPILED_CANDIDATE",
            "freshness": "CURRENT",
            "generation_details": ActionPlanGenerationDetails(
                portfolio_id=portfolio_id,
                alternative_refs=tuple(item.action_id for item in candidate.alternatives),
                decision_analysis=candidate.decision_analysis,
                generated_from_head_set=candidate.plan_revision_digest,
                next_action_order=candidate.next_action_order,
                last_r2_execution=candidate.last_r2_execution,
                recovery_candidate=candidate.recovery_candidate,
            ),
            "supersedes_revision_digest": parent,
            "created_at": created_at,
            "schema_version": "1.1.0",
            "revision_digest": "0" * 64,
        }
    )
    record = ActionPlanRecord.model_validate(draft)
    digest = domain_digest(
        "ACTION_PLAN",
        "2.0.0",
        canonical_payload(record.model_dump(mode="python", exclude={"revision_digest"})),
    )
    return record.model_copy(update={"revision_digest": digest})
