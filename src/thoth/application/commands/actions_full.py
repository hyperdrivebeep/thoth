from __future__ import annotations

from typing import cast

from pydantic import Field, JsonValue

from thoth.application.commands.action_generation import GenerateInput as GenerateInput
from thoth.application.commands.action_generation import generate_actions
from thoth.application.services.action_currentness import action_read_view, plan_read_view
from thoth.application.services.action_service import ActionService
from thoth.application.services.revision_service import CommitResult
from thoth.domain.action_full import ActionPlanRecord, ActionPortfolioRecord, ActionRecord
from thoth.domain.base import DomainModel
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.ports.action import ActionStorePort
from thoth.ports.decision_object import DecisionObjectStorePort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


class ProjectInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)


class ActionListInput(ProjectInput):
    object_id: str | None = Field(default=None, max_length=160)
    portfolio_id: str | None = Field(default=None, max_length=160)
    purpose: str | None = Field(default=None, max_length=80)
    proposal_state: str | None = Field(default=None, max_length=80)
    policy_state: str | None = Field(default=None, max_length=80)
    authorization_state: str | None = Field(default=None, max_length=80)


class ActionReadInput(ProjectInput):
    action_id: str = Field(min_length=1, max_length=160)
    revision_digest: str | None = Field(default=None, min_length=64, max_length=64)


class PortfolioListInput(ProjectInput):
    object_id: str | None = Field(default=None, max_length=160)
    decision_state: str | None = Field(default=None, max_length=80)


class PortfolioReadInput(ProjectInput):
    portfolio_id: str = Field(min_length=1, max_length=160)
    revision_digest: str | None = Field(default=None, min_length=64, max_length=64)


class PlanReadInput(ProjectInput):
    plan_id: str = Field(min_length=1, max_length=160)
    revision_digest: str | None = Field(default=None, min_length=64, max_length=64)


class StepReadInput(PlanReadInput):
    step_id: str = Field(min_length=1, max_length=160)


class ImpactReadInput(ProjectInput):
    action_id: str | None = Field(default=None, max_length=160)
    plan_id: str | None = Field(default=None, max_length=160)
    step_id: str | None = Field(default=None, max_length=160)
    revision_digest: str | None = Field(default=None, min_length=64, max_length=64)


class AuthorizationReadInput(ProjectInput):
    authorization_id: str = Field(min_length=1, max_length=160)


class AuditReadInput(ProjectInput):
    action_id: str | None = Field(default=None, max_length=160)
    plan_id: str | None = Field(default=None, max_length=160)
    revision_digest: str | None = Field(default=None, min_length=64, max_length=64)


class CreateInput(ProjectInput):
    object_id: str = Field(min_length=1, max_length=160)
    portfolio_id: str | None = Field(default=None, max_length=160)
    hypothesis_refs: tuple[str, ...] = ()
    primary_purpose: str = Field(min_length=1, max_length=80)
    secondary_purposes: tuple[str, ...] = ()
    specification: dict[str, JsonValue]
    evidence_refs: tuple[str, ...]
    expected_object_revision: str | None = Field(default=None, min_length=64, max_length=64)


class ActionRevisionBound(ActionReadInput):
    expected_revision_digest: str = Field(min_length=64, max_length=64)


class ReviseInput(ActionRevisionBound):
    patch: dict[str, JsonValue]
    evidence_refs: tuple[str, ...]
    reason: str = Field(min_length=1, max_length=5_000)


class PortfolioComposeInput(ProjectInput):
    object_id: str = Field(min_length=1, max_length=160)
    action_ids: tuple[str, ...] = Field(min_length=2)
    decision_need: str = Field(min_length=1, max_length=5_000)
    criteria_proposal: tuple[dict[str, JsonValue], ...] = ()
    expected_object_revision: str | None = Field(default=None, min_length=64, max_length=64)
    portfolio_id: str | None = Field(default=None, max_length=160)


class PortfolioEvaluateInput(PortfolioReadInput):
    evaluation_method: str = Field(min_length=1, max_length=160)
    policy_criteria_ref: str = Field(min_length=1, max_length=260)
    preference_inputs: tuple[dict[str, JsonValue], ...] = ()


class RecommendInput(PortfolioReadInput):
    scenario_ref: str | None = Field(default=None, max_length=260)
    rationale: str = Field(min_length=1, max_length=5_000)


class SelectInput(ProjectInput):
    portfolio_id: str = Field(min_length=1, max_length=160)
    action_id: str = Field(min_length=1, max_length=160)
    decision_context: dict[str, JsonValue]
    actor_or_agent_ref: str = Field(min_length=1, max_length=160)
    expected_portfolio_revision: str = Field(min_length=64, max_length=64)


class PlanComposeInput(ProjectInput):
    object_id: str = Field(min_length=1, max_length=160)
    selected_action_refs: tuple[str, ...]
    step_candidates: tuple[dict[str, JsonValue], ...]
    dependency_edges: tuple[dict[str, str], ...]
    expected_object_revision: str | None = Field(default=None, min_length=64, max_length=64)
    plan_id: str | None = Field(default=None, max_length=160)


class PlanRevisionBound(PlanReadInput):
    expected_revision_digest: str = Field(min_length=64, max_length=64)


class PlanReviseInput(PlanRevisionBound):
    patch: dict[str, JsonValue]
    evidence_refs: tuple[str, ...]
    reason: str = Field(min_length=1, max_length=5_000)


class PlanRevalidateInput(PlanReadInput):
    trigger_reason: str = Field(min_length=1, max_length=2_000)


class StepAddInput(ProjectInput):
    plan_id: str = Field(min_length=1, max_length=160)
    step_spec: dict[str, JsonValue]
    dependency_refs: tuple[str, ...]
    expected_plan_revision: str = Field(min_length=64, max_length=64)


class StepReviseInput(ProjectInput):
    plan_id: str = Field(min_length=1, max_length=160)
    step_id: str = Field(min_length=1, max_length=160)
    patch: dict[str, JsonValue]
    evidence_refs: tuple[str, ...]
    reason: str = Field(min_length=1, max_length=5_000)
    expected_plan_revision: str = Field(min_length=64, max_length=64)


class ImpactRecalculateInput(PlanReadInput):
    trigger_refs: tuple[str, ...]


class PolicyClassifyInput(PlanReadInput):
    policy_version: str | None = Field(default=None, max_length=160)


class AuthorizationPrepareInput(ProjectInput):
    plan_id: str = Field(min_length=1, max_length=160)
    step_id: str = Field(min_length=1, max_length=160)
    plan_revision_digest: str = Field(min_length=64, max_length=64)
    predecessor_output_digests: tuple[str, ...]
    target_baseline_digests: tuple[str, ...]
    policy_version: str = Field(min_length=1, max_length=160)


class AuthorizationDecideInput(ProjectInput):
    authorization_id: str = Field(min_length=1, max_length=160)
    decision: str = Field(pattern=r"^(APPROVE|REJECT)$")
    actor_ref: str = Field(min_length=1, max_length=160)
    role_assignment_ref: str = Field(min_length=1, max_length=160)
    approved_digest: str = Field(min_length=64, max_length=64)
    reason: str | None = Field(default=None, max_length=2_000)
    dissent: str | None = Field(default=None, max_length=2_000)


class CompensationCreateInput(ProjectInput):
    caused_by_execution_attempt_ref: str = Field(min_length=1, max_length=260)
    observed_effect_refs: tuple[str, ...]
    intended_mitigation: str = Field(min_length=1, max_length=5_000)
    residual_effect_expectation: str = Field(min_length=1, max_length=5_000)
    evidence_refs: tuple[str, ...]


class MergeProposeInput(ProjectInput):
    action_ids: tuple[str, ...] = Field(min_length=2)
    field_mapping: dict[str, JsonValue]
    evidence_refs: tuple[str, ...]
    rationale: str = Field(min_length=1, max_length=5_000)
    expected_revision_digests: tuple[str, ...] = Field(min_length=2)


class ActionHandlers:
    def __init__(
        self,
        *,
        store: ActionStorePort,
        service: ActionService,
        objects: DecisionObjectStorePort,
    ) -> None:
        self._store = store
        self._service = service
        self._objects = objects

    async def list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ActionListInput.model_validate(value)
        items = tuple(
            item
            for item in self._store.list_actions(request.project_id)
            if (request.object_id is None or item.object_id == request.object_id)
            and (request.portfolio_id is None or item.portfolio_id == request.portfolio_id)
            and (request.purpose is None or item.primary_purpose == request.purpose)
            and (request.proposal_state is None or item.proposal_state == request.proposal_state)
            and (request.policy_state is None or item.policy_state == request.policy_state)
            and (
                request.authorization_state is None
                or item.authorization_state == request.authorization_state
            )
        )
        return {"actions": [self._summary(item) for item in items], "next_cursor": None}

    async def read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ActionReadInput.model_validate(value)
        return action_read_view(self._read_action(request), self._service)

    async def portfolio_list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = PortfolioListInput.model_validate(value)
        items = tuple(
            item
            for item in self._store.list_portfolios(request.project_id)
            if (request.object_id is None or item.object_id == request.object_id)
            and (request.decision_state is None or item.decision_state == request.decision_state)
        )
        return {"portfolios": [item.model_dump(mode="json") for item in items]}

    async def portfolio_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = PortfolioReadInput.model_validate(value)
        return {"portfolio": self._read_portfolio(request).model_dump(mode="json")}

    async def plan_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = PlanReadInput.model_validate(value)
        return plan_read_view(
            self._read_plan(request),
            self._store.list_authorizations(request.project_id, request.plan_id),
            self._service,
        )

    async def plan_graph(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = PlanReadInput.model_validate(value)
        plan = self._read_plan(request)
        return cast(
            dict[str, JsonValue],
            {
                "nodes": list(plan.steps),
                "dependency_edges": list(plan.dependency_edges),
                "gates": [
                    {
                        "step_id": step["step_id"],
                        "policy_state": step.get("policy_state"),
                        "risk_tier": step.get("risk_tier"),
                    }
                    for step in plan.steps
                ],
                "parallel_groups": [list(plan.auto_executable_frontier)],
                "point_of_no_return_steps": list(plan.point_of_no_return_steps),
                "current_frontier": list(plan.auto_executable_frontier),
            },
        )

    async def step_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = StepReadInput.model_validate(value)
        plan = self._read_plan(request)
        return {"step": cast(JsonValue, self._step(plan, request.step_id))}

    async def impact_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ImpactReadInput.model_validate(value)
        if request.action_id:
            action = self._store.read_action(
                request.project_id, request.action_id, request.revision_digest
            )
            if action is None:
                raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "Action not found")
            return cast(
                dict[str, JsonValue],
                {
                    "direct_impact": action.impact_set,
                    "risk_tier": action.risk_tier,
                    "required_processes": list(action.required_processes),
                    "required_roles": list(action.required_roles),
                },
            )
        if request.plan_id:
            plan = self._store.read_plan(
                request.project_id, request.plan_id, request.revision_digest
            )
            if plan is None:
                raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "plan not found")
            if request.step_id:
                return {
                    "step_impact": cast(
                        JsonValue, self._step(plan, request.step_id).get("impact", {})
                    )
                }
            return cast(
                dict[str, JsonValue],
                {
                    "cumulative_impact": plan.cumulative_impact,
                    "required_process_union": list(plan.required_process_union),
                    "required_role_union": list(plan.required_role_union),
                },
            )
        raise RpcApplicationError(
            RpcErrorCode.DOMAIN_REJECTED, "impact query needs action_id or plan_id"
        )

    async def policy_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        result = await self.impact_read(value)
        result["policy_version"] = "project-policy:current"
        return result

    async def authorization_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = AuthorizationReadInput.model_validate(value)
        item = self._store.read_authorization(request.project_id, request.authorization_id)
        if item is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "authorization not found")
        effective_state = (
            "EXPIRED"
            if item.state == "PENDING" and self._service.now() >= item.expires_at
            else item.state
        )
        return {
            "authorization": item.model_dump(mode="json"),
            "effective_state": effective_state,
        }

    async def audit_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = AuditReadInput.model_validate(value)
        subject = request.action_id or request.plan_id
        return {
            "records": [
                item.model_dump(mode="json")
                for item in self._store.list_audit(request.project_id, subject)
            ]
        }

    async def generate(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return await generate_actions(value, service=self._service)

    async def create(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = CreateInput.model_validate(value)
        self._check_object_revision(
            request.project_id, request.object_id, request.expected_object_revision
        )
        try:
            item, duplicates, commit = self._service.create(
                project_id=request.project_id,
                object_id=request.object_id,
                portfolio_id=request.portfolio_id or f"action-portfolio:{request.object_id}",
                hypothesis_refs=request.hypothesis_refs,
                primary_purpose=request.primary_purpose,
                secondary_purposes=request.secondary_purposes,
                specification=cast(dict[str, object], request.specification),
                evidence_refs=request.evidence_refs,
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
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

    async def revise(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ReviseInput.model_validate(value)
        current = self._read_action_expected(request)
        allowed = {
            "primary_purpose",
            "secondary_purposes",
            "specification",
            "evidence_refs",
            "expected_observation_or_change",
        }
        rejected = sorted(set(request.patch) - allowed)
        if rejected:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED,
                f"noncanonical Action patch: {', '.join(rejected)}",
            )
        updates: dict[str, object] = {
            key: tuple(child) if key.endswith("_refs") and isinstance(child, list) else child
            for key, child in request.patch.items()
        }
        revised, commit = self._revise_action(
            current,
            updates=updates,
            event_type="action/updated",
            evidence_refs=request.evidence_refs,
            invalidate_downstream=True,
        )
        return self._action_revision_result(current, revised, commit)

    async def portfolio_compose(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = PortfolioComposeInput.model_validate(value)
        self._check_object_revision(
            request.project_id, request.object_id, request.expected_object_revision
        )
        try:
            portfolio, commit = self._service.compose_portfolio(
                project_id=request.project_id,
                object_id=request.object_id,
                action_ids=request.action_ids,
                decision_need=request.decision_need,
                criteria_proposal=tuple(
                    cast(dict[str, object], item) for item in request.criteria_proposal
                ),
                portfolio_id=request.portfolio_id,
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return cast(
            dict[str, JsonValue],
            {
                "portfolio": portfolio.model_dump(mode="json"),
                "missing_alternatives": [],
                "missing_criteria": [],
                "commit": commit.model_dump(mode="json"),
            },
        )

    async def portfolio_evaluate(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = PortfolioEvaluateInput.model_validate(value)
        current = self._read_portfolio(request)
        try:
            revised, commit = self._service.evaluate_portfolio(
                current,
                evaluation_method=request.evaluation_method,
                policy_criteria_ref=request.policy_criteria_ref,
                preference_inputs=tuple(
                    cast(dict[str, object], item) for item in request.preference_inputs
                ),
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
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

    async def recommend(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = RecommendInput.model_validate(value)
        current = self._read_portfolio(request)
        try:
            revised, commit = self._service.recommend(current, request.rationale)
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
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

    async def select(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = SelectInput.model_validate(value)
        current = self._portfolio_expected(
            request.project_id,
            request.portfolio_id,
            request.expected_portfolio_revision,
        )
        try:
            action, portfolio, action_commit, portfolio_commit = self._service.select(
                current,
                action_id=request.action_id,
                decision_context=cast(dict[str, object], request.decision_context),
                actor_ref=request.actor_or_agent_ref,
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
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

    async def plan_compose(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = PlanComposeInput.model_validate(value)
        self._check_object_revision(
            request.project_id, request.object_id, request.expected_object_revision
        )
        try:
            plan, commit = self._service.compose_plan(
                project_id=request.project_id,
                object_id=request.object_id,
                selected_action_refs=request.selected_action_refs,
                step_candidates=tuple(
                    cast(dict[str, object], item) for item in request.step_candidates
                ),
                dependency_edges=request.dependency_edges,
                plan_id=request.plan_id,
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return cast(
            dict[str, JsonValue],
            {
                "plan": plan.model_dump(mode="json"),
                "profile_schema_impact_policy_validation": plan.validation_state,
                "initial_frontier": list(plan.auto_executable_frontier),
                "commit": commit.model_dump(mode="json"),
            },
        )

    async def plan_revise(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = PlanReviseInput.model_validate(value)
        current = self._plan_expected(
            request.project_id, request.plan_id, request.expected_revision_digest
        )
        allowed = {"steps", "dependency_edges", "selected_action_refs"}
        rejected = sorted(set(request.patch) - allowed)
        if rejected:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED,
                f"noncanonical plan patch: {', '.join(rejected)}",
            )
        updates: dict[str, object] = {
            key: tuple(child) if isinstance(child, list) else child
            for key, child in request.patch.items()
        }
        revised, commit = self._revise_plan(
            current,
            updates=updates,
            event_type="action/planUpdated",
            evidence_refs=request.evidence_refs,
        )
        return self._plan_revision_result(current, revised, commit)

    async def plan_revalidate(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = PlanRevalidateInput.model_validate(value)
        current = self._read_plan(request)
        revised, commit = self._revise_plan(
            current,
            updates={"validation_state": "VALIDATED"},
            event_type="action/planUpdated",
        )
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

    async def step_add(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = StepAddInput.model_validate(value)
        current = self._plan_expected(
            request.project_id, request.plan_id, request.expected_plan_revision
        )
        step = cast(dict[str, object], request.step_spec)
        step_id = str(step.get("step_id") or "")
        edges = tuple(
            (
                *current.dependency_edges,
                *({"from": ref, "to": step_id} for ref in request.dependency_refs),
            )
        )
        revised, commit = self._revise_plan(
            current,
            updates={"steps": (*current.steps, step), "dependency_edges": edges},
            event_type="action/planUpdated",
        )
        return cast(
            dict[str, JsonValue],
            {
                "plan": revised.model_dump(mode="json"),
                "step": self._step(revised, step_id),
                "frontier_delta": list(revised.auto_executable_frontier),
                "commit": commit.model_dump(mode="json"),
            },
        )

    async def step_revise(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = StepReviseInput.model_validate(value)
        current = self._plan_expected(
            request.project_id, request.plan_id, request.expected_plan_revision
        )
        found = False
        steps: list[dict[str, object]] = []
        for step in current.steps:
            if step.get("step_id") == request.step_id:
                steps.append({**step, **cast(dict[str, object], request.patch)})
                found = True
            else:
                steps.append(step)
        if not found:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "step not found")
        revised, commit = self._revise_plan(
            current,
            updates={"steps": tuple(steps)},
            event_type="action/planUpdated",
            evidence_refs=request.evidence_refs,
        )
        return cast(
            dict[str, JsonValue],
            {
                "plan": revised.model_dump(mode="json"),
                "step": self._step(revised, request.step_id),
                "invalidated_authorizations_executions": "ALL_BOUND_TO_PREVIOUS_PLAN_REVISION",
                "cumulative_impact": revised.cumulative_impact,
                "commit": commit.model_dump(mode="json"),
            },
        )

    async def impact_recalculate(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ImpactRecalculateInput.model_validate(value)
        current = self._read_plan(request)
        revised, commit = self._revise_plan(
            current,
            updates={},
            event_type="action/impactChanged",
        )
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

    async def policy_classify(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = PolicyClassifyInput.model_validate(value)
        current = self._read_plan(request)
        revised, commit = self._revise_plan(
            current,
            updates={},
            event_type="action/policyChanged",
        )
        return cast(
            dict[str, JsonValue],
            {
                "plan": revised.model_dump(mode="json"),
                "risk_tiers": {
                    str(step["step_id"]): step.get("risk_tier") for step in revised.steps
                },
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

    async def authorization_prepare(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = AuthorizationPrepareInput.model_validate(value)
        plan = self._store.read_plan(
            request.project_id, request.plan_id, request.plan_revision_digest
        )
        if plan is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "plan revision not found")
        try:
            envelope, state = self._service.prepare_authorization(
                plan,
                step_id=request.step_id,
                predecessor_output_digests=request.predecessor_output_digests,
                target_baseline_digests=request.target_baseline_digests,
                policy_version=request.policy_version,
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return {
            "authorization": None if envelope is None else envelope.model_dump(mode="json"),
            "state": state,
            "preflight_requirements": {
                "predecessor_outputs": list(request.predecessor_output_digests),
                "target_baselines": list(request.target_baseline_digests),
            },
        }

    async def authorization_decide(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = AuthorizationDecideInput.model_validate(value)
        current = self._store.read_authorization(request.project_id, request.authorization_id)
        if current is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "authorization not found")
        try:
            decided = self._service.decide_authorization(
                current,
                decision=request.decision,
                actor_ref=request.actor_ref,
                role_assignment_ref=request.role_assignment_ref,
                approved_digest=request.approved_digest,
                reason=request.reason,
                dissent=request.dissent,
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return {
            "authorization": decided.model_dump(mode="json"),
            "exact_scope_digest": decided.exact_scope_digest,
            "single_use": decided.single_use,
        }

    async def compensation_create(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = CompensationCreateInput.model_validate(value)
        objects = self._objects.list_objects(request.project_id)
        if len(objects) != 1:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED,
                "compensation requires an unambiguous DecisionObject context",
            )
        specification: dict[str, object] = {
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
        try:
            action, _duplicates, commit = self._service.create(
                project_id=request.project_id,
                object_id=objects[0].object_id,
                portfolio_id=f"action-portfolio:{objects[0].object_id}",
                hypothesis_refs=(),
                primary_purpose="RESTORE_COMPENSATE",
                secondary_purposes=("RISK_REDUCTION",),
                specification=specification,
                evidence_refs=request.evidence_refs,
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return {
            "action": action.model_dump(mode="json"),
            "exact_rollback_claimed": False,
            "residual_risk_contract": request.residual_effect_expectation,
            "commit": commit.model_dump(mode="json"),
        }

    async def merge_propose(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = MergeProposeInput.model_validate(value)
        if len(request.action_ids) != len(request.expected_revision_digests):
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED,
                "Action IDs and expected revision digests must align",
            )
        actions = tuple(
            self._store.read_action(request.project_id, action_id, None)
            for action_id in request.action_ids
        )
        if any(action is None for action in actions):
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "Action not found")
        actual = tuple(action.revision_digest for action in actions if action is not None)
        if actual != request.expected_revision_digests:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "revision mismatch")
        purposes = {action.primary_purpose for action in actions if action is not None}
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
        first = cast(ActionRecord, actions[0])
        self._service.audit(
            request.project_id,
            first.action_id,
            "action/mergeProposed",
            {**payload, "digest": digest},
        )
        return cast(
            dict[str, JsonValue],
            {
                "merge_proposal": {**payload, "digest": digest},
                "impact_preview": [action.impact_set for action in actions if action is not None],
                "lineage_preview": [f"SUPERSEDES:{item}" for item in request.action_ids],
            },
        )

    def _read_action(self, request: ActionReadInput) -> ActionRecord:
        item = self._store.read_action(
            request.project_id, request.action_id, request.revision_digest
        )
        if item is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "Action not found")
        return item

    def _read_action_expected(self, request: ActionRevisionBound) -> ActionRecord:
        item = self._store.read_action(request.project_id, request.action_id, None)
        if item is None or item.revision_digest != request.expected_revision_digest:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "Action revision changed")
        return item

    def _read_portfolio(self, request: PortfolioReadInput) -> ActionPortfolioRecord:
        item = self._store.read_portfolio(
            request.project_id, request.portfolio_id, request.revision_digest
        )
        if item is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "portfolio not found")
        return item

    def _portfolio_expected(
        self, project_id: str, portfolio_id: str, expected: str
    ) -> ActionPortfolioRecord:
        item = self._store.read_portfolio(project_id, portfolio_id, None)
        if item is None or item.revision_digest != expected:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "portfolio revision changed")
        return item

    def _read_plan(self, request: PlanReadInput) -> ActionPlanRecord:
        item = self._store.read_plan(request.project_id, request.plan_id, request.revision_digest)
        if item is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "plan not found")
        return item

    def _plan_expected(self, project_id: str, plan_id: str, expected: str) -> ActionPlanRecord:
        item = self._store.read_plan(project_id, plan_id, None)
        if item is None or item.revision_digest != expected:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "plan revision changed")
        return item

    def _check_object_revision(self, project_id: str, object_id: str, expected: str | None) -> None:
        if expected is None:
            return
        item = self._objects.read_object(project_id, object_id, None)
        if item is None or item.revision_digest != expected:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED, "DecisionObject revision changed"
            )

    def _revise_action(
        self,
        current: ActionRecord,
        *,
        updates: dict[str, object],
        event_type: str,
        evidence_refs: tuple[str, ...] = (),
        invalidate_downstream: bool = False,
    ) -> tuple[ActionRecord, CommitResult]:
        try:
            return self._service.revise(
                current,
                updates=updates,
                event_type=event_type,
                evidence_refs=evidence_refs,
                invalidate_downstream=invalidate_downstream,
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc

    def _revise_plan(
        self,
        current: ActionPlanRecord,
        *,
        updates: dict[str, object],
        event_type: str,
        evidence_refs: tuple[str, ...] = (),
    ) -> tuple[ActionPlanRecord, CommitResult]:
        try:
            return self._service.revise_plan(
                current,
                updates=updates,
                event_type=event_type,
                evidence_refs=evidence_refs,
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc

    @staticmethod
    def _step(plan: ActionPlanRecord, step_id: str) -> dict[str, object]:
        for step in plan.steps:
            if step.get("step_id") == step_id:
                return step
        raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "step not found")

    @staticmethod
    def _summary(item: ActionRecord) -> dict[str, JsonValue]:
        return {
            "action_id": item.action_id,
            "object_id": item.object_id,
            "portfolio_id": item.portfolio_id,
            "primary_purpose": item.primary_purpose,
            "proposal_state": item.proposal_state,
            "decision_state": item.decision_state,
            "policy_state": item.policy_state,
            "authorization_state": item.authorization_state,
            "risk_tier": item.risk_tier,
            "freshness": item.freshness,
            "revision_digest": item.revision_digest,
        }

    @staticmethod
    def _action_revision_result(
        before: ActionRecord, after: ActionRecord, commit: CommitResult
    ) -> dict[str, JsonValue]:
        return cast(
            dict[str, JsonValue],
            {
                "action": after.model_dump(mode="json"),
                "semantic_diff": ActionHandlers._diff(before, after),
                "invalidated_decisions_plans_authorizations_executions": True,
                "commit": commit.model_dump(mode="json"),
            },
        )

    @staticmethod
    def _plan_revision_result(
        before: ActionPlanRecord, after: ActionPlanRecord, commit: CommitResult
    ) -> dict[str, JsonValue]:
        return cast(
            dict[str, JsonValue],
            {
                "plan": after.model_dump(mode="json"),
                "semantic_diff": ActionHandlers._diff(before, after),
                "frontier_impact_auth_invalidations": True,
                "commit": commit.model_dump(mode="json"),
            },
        )

    @staticmethod
    def _diff(before: DomainModel, after: DomainModel) -> list[dict[str, JsonValue]]:
        left = before.model_dump(mode="json")
        right = after.model_dump(mode="json")
        return [
            {
                "path": key,
                "before": cast(JsonValue, left.get(key)),
                "after": cast(JsonValue, right.get(key)),
            }
            for key in sorted(set(left) | set(right))
            if left.get(key) != right.get(key)
        ]
