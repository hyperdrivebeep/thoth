"""Scoped coverage effects: preserve investigatory alternatives and qualify promotion."""

from thoth.domain.action import ActionPlan
from thoth.domain.evidence_requirements import CoverageAssessment, HypothesisSemanticReview
from thoth.domain.research_execution import ResearchWork


def blocked_targets(work: ResearchWork) -> frozenset[str]:
    value = work.context.get("coverage")
    if value is None:
        return frozenset()
    coverage = CoverageAssessment.model_validate(value)
    return frozenset(target for target, state in coverage.gates.items() if state == "HOLD")


def qualify_hypothesis_review(
    review: HypothesisSemanticReview, work: ResearchWork
) -> HypothesisSemanticReview:
    blocked = blocked_targets(work)
    return review.model_copy(
        update={
            "decisions": tuple(
                decision.model_copy(
                    update={
                        "relation": "INCONCLUSIVE",
                        "gaps": (*decision.gaps, "GOVERNING_COVERAGE_UNRESOLVED"),
                    }
                )
                if blocked.intersection(
                    {
                        "hypothesis",
                        "hypothesis_promotion",
                        "profile_dependent_promotion",
                        decision.hypothesis_id,
                    }
                )
                else decision
                for decision in review.decisions
            )
        }
    )


def qualify_action_frontier(plan: ActionPlan, work: ResearchWork) -> ActionPlan:
    blocked = blocked_targets(work)
    held = {
        action.action_id
        for action in plan.alternatives
        if blocked.intersection({"action", "execution", action.action_id, action.action_family})
    }
    work.context["action_coverage_holds"] = sorted(held)
    return plan.model_copy(
        update={
            "frontier": tuple(ref for ref in plan.frontier if ref not in held),
            "next_action_order": tuple(ref for ref in plan.next_action_order if ref not in held),
        }
    )
