"""Register the hypothesis, action, and execution decision-chain RPCs."""

from thoth.application.commands import (
    ActionHandlers,
    ExecutionHandlers,
    HypothesisHandlers,
    ProjectionQueryHandlers,
)
from thoth.protocol.registry import MethodRegistry


def register_decision_chain_methods(
    registry: MethodRegistry,
    hypothesis_handlers: HypothesisHandlers,
    action_handlers: ActionHandlers,
    execution_handlers: ExecutionHandlers,
    projection_handlers: ProjectionQueryHandlers,
) -> None:
    registry.register("hypothesis/list", hypothesis_handlers.list)
    registry.register("hypothesis/read", hypothesis_handlers.read)
    registry.register("hypothesis/graph/read", hypothesis_handlers.graph_read)
    registry.register("hypothesis/portfolio/list", hypothesis_handlers.portfolio_list)
    registry.register("hypothesis/portfolio/read", hypothesis_handlers.portfolio_read)
    registry.register("hypothesis/relation/list", hypothesis_handlers.relation_list)
    registry.register("hypothesis/prediction/list", hypothesis_handlers.prediction_list)
    registry.register("hypothesis/prediction/read", hypothesis_handlers.prediction_read)
    registry.register("hypothesis/assumption/list", hypothesis_handlers.assumption_list)
    registry.register("hypothesis/quality/read", hypothesis_handlers.quality_read)
    registry.register("hypothesis/appraisal/read", hypothesis_handlers.appraisal_read)
    registry.register("hypothesis/audit/read", hypothesis_handlers.audit_read)
    registry.register("hypothesis/generate", hypothesis_handlers.generate)
    registry.register("hypothesis/create", hypothesis_handlers.create)
    registry.register("hypothesis/revise", hypothesis_handlers.revise)
    registry.register("hypothesis/intent/update", hypothesis_handlers.intent_update)
    registry.register("hypothesis/causal/update", hypothesis_handlers.causal_update)
    registry.register("hypothesis/relation/add", hypothesis_handlers.relation_add)
    registry.register("hypothesis/relation/remove", hypothesis_handlers.relation_remove)
    registry.register("hypothesis/assumption/add", hypothesis_handlers.assumption_add)
    registry.register("hypothesis/prediction/bind", hypothesis_handlers.prediction_bind)
    registry.register(
        "hypothesis/counterevidence/request",
        hypothesis_handlers.counterevidence_request,
    )
    registry.register("hypothesis/portfolio/compose", hypothesis_handlers.portfolio_compose)
    registry.register(
        "hypothesis/portfolio/revalidate", hypothesis_handlers.portfolio_revalidate
    )
    registry.register("hypothesis/test/bind", hypothesis_handlers.test_bind)
    registry.register("hypothesis/appraise", hypothesis_handlers.appraise)
    registry.register("hypothesis/split/propose", hypothesis_handlers.split_propose)
    registry.register("hypothesis/merge/propose", hypothesis_handlers.merge_propose)
    registry.register("action/list", action_handlers.list)
    registry.register("action/read", action_handlers.read)
    registry.register("action/portfolio/list", action_handlers.portfolio_list)
    registry.register("action/portfolio/read", action_handlers.portfolio_read)
    registry.register("action/plan/read", action_handlers.plan_read)
    registry.register("action/plan/graph", action_handlers.plan_graph)
    registry.register("action/step/read", action_handlers.step_read)
    registry.register("action/impact/read", action_handlers.impact_read)
    registry.register("action/policy/read", action_handlers.policy_read)
    registry.register("action/authorization/read", action_handlers.authorization_read)
    registry.register("action/audit/read", action_handlers.audit_read)
    registry.register("action/generate", action_handlers.generate)
    registry.register("action/create", action_handlers.create)
    registry.register("action/revise", action_handlers.revise)
    registry.register("action/portfolio/compose", action_handlers.portfolio_compose)
    registry.register("action/portfolio/evaluate", action_handlers.portfolio_evaluate)
    registry.register("action/recommend", action_handlers.recommend)
    registry.register("action/select", action_handlers.select)
    registry.register("action/plan/compose", action_handlers.plan_compose)
    registry.register("action/plan/revise", action_handlers.plan_revise)
    registry.register("action/plan/revalidate", action_handlers.plan_revalidate)
    registry.register("action/step/add", action_handlers.step_add)
    registry.register("action/step/revise", action_handlers.step_revise)
    registry.register("action/impact/recalculate", action_handlers.impact_recalculate)
    registry.register("action/policy/classify", action_handlers.policy_classify)
    registry.register("action/authorization/prepare", action_handlers.authorization_prepare)
    registry.register("action/authorization/decide", action_handlers.authorization_decide)
    registry.register("action/compensation/create", action_handlers.compensation_create)
    registry.register("action/merge/propose", action_handlers.merge_propose)
    registry.register("execution/list", execution_handlers.list)
    registry.register("execution/read", execution_handlers.read)
    registry.register("execution/frontier/read", execution_handlers.frontier_read)
    registry.register("execution/preflight/read", execution_handlers.preflight_read)
    registry.register("execution/attempt/list", execution_handlers.attempt_list)
    registry.register("execution/attempt/read", execution_handlers.attempt_read)
    registry.register("execution/effect/read", execution_handlers.effect_read)
    registry.register("execution/reconciliation/read", execution_handlers.reconciliation_read)
    registry.register("execution/audit/read", execution_handlers.audit_read)
    registry.register("execution/start", execution_handlers.start)
    registry.register("execution/pause", execution_handlers.pause)
    registry.register("execution/resume", execution_handlers.resume)
    registry.register("execution/cancel", execution_handlers.cancel)
    registry.register("execution/retry", execution_handlers.retry)
    registry.register("execution/reconcile", execution_handlers.reconcile)
    registry.register("execution/observation/link", execution_handlers.observation_link)
    registry.register("execution/compensation/propose", execution_handlers.compensation_propose)
    registry.register("execution/invalidate", execution_handlers.invalidate)
    registry.register("action/preflight/read", projection_handlers.action_preflight)
