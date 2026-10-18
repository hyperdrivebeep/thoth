from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from thoth.application.commands.action_create_input import CreateInput
from thoth.application.commands.action_effort import revise_action
from thoth.application.commands.action_generation import GenerateInput as GenerateInput
from thoth.application.commands.action_generation import generate_actions
from thoth.application.commands.action_inputs import (
    ActionListInput,
    ActionReadInput,
    ActionRevisionBound,
    AuditReadInput,
    AuthorizationDecideInput,
    AuthorizationPrepareInput,
    AuthorizationReadInput,
    CompensationCreateInput,
    ImpactReadInput,
    ImpactRecalculateInput,
    MergeProposeInput,
    PlanComposeInput,
    PlanReadInput,
    PlanRevalidateInput,
    PlanReviseInput,
    PolicyClassifyInput,
    PortfolioComposeInput,
    PortfolioEvaluateInput,
    PortfolioListInput,
    PortfolioReadInput,
    RecommendInput,
    ReviseInput,
    SelectInput,
    StepAddInput,
    StepReadInput,
    StepReviseInput,
)
from thoth.application.commands.action_mutation_projection import (
    authorization_decide_result,
    authorization_prepare_result,
    compensation_result,
    compensation_specification,
    create_result,
    impact_recalculate_result,
    merge_proposal_payload,
    merge_proposal_result,
    plan_compose_result,
    plan_revalidate_result,
    policy_classify_result,
    portfolio_compose_result,
    portfolio_evaluate_result,
    recommend_result,
    select_result,
    step_add_result,
    step_revise_result,
)
from thoth.application.commands.action_read_projection import (
    action_matches,
    action_revision_result,
    action_summary,
    plan_graph_view,
    plan_revision_result,
    plan_step,
    portfolio_matches,
    semantic_diff,
)
from thoth.application.commands.action_read_queries import read_impact
from thoth.application.commands.action_step_input import step_with_id
from thoth.application.services.action_currentness import action_read_view, plan_read_view
from thoth.application.services.action_service import ActionService
from thoth.application.services.authorization_currentness import authorization_read_view
from thoth.application.services.revision_service import CommitResult
from thoth.domain.action_full import ActionPlanRecord, ActionPortfolioRecord, ActionRecord
from thoth.ports.action import ActionStorePort
from thoth.ports.decision_object import DecisionObjectStorePort
from thoth.ports.operation import OperationStorePort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


class ActionHandlers:
    def __init__(
        self,
        *,
        store: ActionStorePort,
        service: ActionService,
        objects: DecisionObjectStorePort,
        operations: OperationStorePort | None = None,
    ) -> None:
        self._store = store
        self._service = service
        self._objects = objects
        self._operations = operations

    async def list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ActionListInput.model_validate(value)
        items = tuple(
            item
            for item in self._store.list_actions(request.project_id)
            if action_matches(item, request)
        )
        return {"actions": [self._summary(item) for item in items], "next_cursor": None}

    async def read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ActionReadInput.model_validate(value)
        return action_read_view(self._read_action(request), self._service, self._operations)

    async def portfolio_list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = PortfolioListInput.model_validate(value)
        items = tuple(
            item
            for item in self._store.list_portfolios(request.project_id)
            if portfolio_matches(item, request)
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
        return plan_graph_view(plan)

    async def step_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = StepReadInput.model_validate(value)
        plan = self._read_plan(request)
        return {"step": cast(JsonValue, self._step(plan, request.step_id))}

    async def impact_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ImpactReadInput.model_validate(value)
        return read_impact(self._store, request)

    async def policy_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        result = await self.impact_read(value)
        result["policy_version"] = "project-policy:current"
        return result

    async def authorization_read(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = AuthorizationReadInput.model_validate(value)
        item = self._store.read_authorization(request.project_id, request.authorization_id)
        if item is None:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "authorization not found")
        plan = self._store.read_plan(item.project_id, item.plan_id, None)
        return authorization_read_view(
            item, plan, now=self._service.now(), currentness=self._service.currentness
        )

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
                test_refs=request.test_refs,
            )
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return create_result(item, duplicates, commit)

    async def revise(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ReviseInput.model_validate(value)
        current = self._read_action_expected(request)
        revised, commit = revise_action(
            self._revise_action,
            self._service,
            self._operations,
            current,
            request,
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
        return portfolio_compose_result(portfolio, commit)

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
        return portfolio_evaluate_result(revised, commit)

    async def recommend(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = RecommendInput.model_validate(value)
        current = self._read_portfolio(request)
        try:
            revised, commit = self._service.recommend(current, request.rationale)
        except ValueError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, str(exc)) from exc
        return recommend_result(revised, commit)

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
        return select_result(request, action, portfolio, action_commit, portfolio_commit)

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
        return plan_compose_result(plan, commit)

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
        return plan_revalidate_result(revised, request, commit)

    async def step_add(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = StepAddInput.model_validate(value)
        step, step_id = step_with_id(request.step_spec)
        current = self._plan_expected(
            request.project_id, request.plan_id, request.expected_plan_revision
        )
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
        return step_add_result(revised, step_id, commit)

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
        return step_revise_result(revised, request, commit)

    async def impact_recalculate(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ImpactRecalculateInput.model_validate(value)
        current = self._read_plan(request)
        revised, commit = self._revise_plan(
            current,
            updates={},
            event_type="action/impactChanged",
        )
        return impact_recalculate_result(revised, request, commit)

    async def policy_classify(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = PolicyClassifyInput.model_validate(value)
        current = self._read_plan(request)
        revised, commit = self._revise_plan(
            current,
            updates={},
            event_type="action/policyChanged",
        )
        return policy_classify_result(revised, request, commit)

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
        return authorization_prepare_result(envelope, state, request)

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
        return authorization_decide_result(decided, current)

    async def compensation_create(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = CompensationCreateInput.model_validate(value)
        objects = self._objects.list_objects(request.project_id)
        if len(objects) != 1:
            raise RpcApplicationError(
                RpcErrorCode.DOMAIN_REJECTED,
                "compensation requires an unambiguous DecisionObject context",
            )
        specification = compensation_specification(request)
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
        return compensation_result(request, action, commit)

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
        payload, digest = merge_proposal_payload(request, purposes)
        first = cast(ActionRecord, actions[0])
        self._service.audit(
            request.project_id,
            first.action_id,
            "action/mergeProposed",
            {**payload, "digest": digest},
        )
        return merge_proposal_result(payload, digest, actions, request)

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

    _step = staticmethod(plan_step)

    _summary = staticmethod(action_summary)

    _action_revision_result = staticmethod(action_revision_result)

    _plan_revision_result = staticmethod(plan_revision_result)

    _diff = staticmethod(semantic_diff)
