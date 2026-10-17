"""Pure cycle preparation over values already captured by ThreadCycleService.execute."""

from thoth.application.services.research_identity_service import ResearchContext
from thoth.application.workflows.thread_cycle_contracts import ThreadCycleCommand
from thoth.domain.enums import HypothesisStatus, PortfolioStatus
from thoth.domain.evidence import InformationSufficiencyAssessment
from thoth.domain.hypothesis import HypothesisPortfolio
from thoth.domain.model import ContextPack
from thoth.domain.revision import StagedRevision


def draft_portfolio(
    portfolio: HypothesisPortfolio,
    review_id: str | None,
) -> HypothesisPortfolio:
    return portfolio.model_copy(
        update={
            "status": PortfolioStatus.DRAFT,
            "hypotheses": tuple(
                h.model_copy(
                    update={
                        "status": HypothesisStatus.DRAFT,
                        "semantic_review_ref": review_id,
                    }
                )
                for h in portfolio.hypotheses
            ),
        }
    )


def expected_cycle_heads(
    heads: dict[str, str],
    staged: tuple[StagedRevision, ...],
    expected_head_overrides: dict[str, str] | None,
) -> dict[str, str]:
    staged_keys = {
        f"{item.revision.entity_type.value}:{item.revision.entity_id}" for item in staged
    }
    expected_heads = {key: value for key, value in heads.items() if key in staged_keys}
    expected_heads.update(expected_head_overrides or {})
    return expected_heads


def build_model_context(
    command: ThreadCycleCommand,
    assessment: InformationSufficiencyAssessment,
    head_digest: str,
    research: ResearchContext,
) -> ContextPack:
    return ContextPack(
        case_id=command.case_id,
        project_id=command.project_id,
        object_id=command.object_id,
        problem=command.problem,
        evidence=command.evidence,
        criteria=command.criteria,
        sufficiency=assessment,
        input_head_set_digest=head_digest,
        policy_hints={
            "minimum_action_tier_by_family": {
                family: tier.value
                for family, tier in command.action_policy.minimum_tier_by_family.items()
            },
            "approver_role_by_family": command.action_policy.approver_role_by_family,
            "unknown_family_tier": command.action_policy.unknown_family_tier.value,
        },
        research_context={
            "entity_currentness": {
                key: value.model_dump(mode="json") for key, value in research.currentness.items()
            }
        },
        previous_portfolio=research.portfolio_view,
        previous_action_plan=research.action_plan_view,
        canonical_hypotheses=research.hypotheses,
        canonical_portfolio=research.portfolio,
        canonical_actions=research.actions,
        canonical_action_plan=research.action_plan,
        canonical_test_assessments=research.test_assessments,
        unresolved_research_refs=research.unrepresented_refs,
    )
