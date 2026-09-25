from __future__ import annotations

from collections.abc import Mapping

from thoth.application.reducers.authority_router import ActionRiskFacts, validate_action_route
from thoth.domain.action import ActionPlan
from thoth.domain.errors import InvariantViolation
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.hypothesis import HypothesisPortfolio


def validate_action_plan(
    plan: ActionPlan,
    *,
    portfolio: HypothesisPortfolio,
    evidence: tuple[EvidenceSpan, ...],
    risk_facts: Mapping[str, ActionRiskFacts],
) -> ActionPlan:
    if plan.object_id != portfolio.object_id:
        raise InvariantViolation("action plan belongs to a different decision object")
    hypothesis_ids = {hypothesis.hypothesis_id for hypothesis in portfolio.hypotheses}
    evidence_ids = {span.span_id for span in evidence}
    for action in plan.alternatives:
        if not set(action.hypothesis_ids).issubset(hypothesis_ids):
            raise InvariantViolation("action references a hypothesis outside the portfolio")
        if not set(action.source_refs).issubset(evidence_ids):
            raise InvariantViolation("action references evidence outside the context pack")
        try:
            facts = risk_facts[action.action_id]
        except KeyError as exc:
            raise InvariantViolation("action has no deterministic risk facts") from exc
        validate_action_route(action, facts)
    return plan
