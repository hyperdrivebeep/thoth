"""Initial six full families. Adding a profile does not change planner dispatch."""

from thoth.application.services.restore_profiles import RestoreProfileRegistry, TypedRestoreProfile
from thoth.domain.action_full import ActionPlanRecord, ActionPortfolioRecord, ActionRecord
from thoth.domain.enums import EntityType
from thoth.domain.evidence import InformationSufficiencyAssessment
from thoth.domain.hypothesis_full import HypothesisPortfolioRecord, HypothesisRecord


def restore_profiles() -> RestoreProfileRegistry:
    return RestoreProfileRegistry(
        (
            TypedRestoreProfile(
                "evidence-assessment.v1",
                InformationSufficiencyAssessment,
                EntityType.EVIDENCE,
                "assessment_id",
                "target_object_id",
                display_name="근거 충분성 평가",
            ),
            TypedRestoreProfile(
                "hypothesis.v1",
                HypothesisRecord,
                EntityType.HYPOTHESIS,
                "hypothesis_id",
                display_name="가설",
            ),
            TypedRestoreProfile(
                "hypothesis-portfolio.v1",
                HypothesisPortfolioRecord,
                EntityType.HYPOTHESIS,
                "portfolio_id",
                reference_fields=(("hypothesis_refs", "HYPOTHESIS"),),
                display_name="가설 구성",
            ),
            TypedRestoreProfile(
                "action.v1",
                ActionRecord,
                EntityType.ACTION,
                "action_id",
                display_name="행동 제안",
                reference_fields=(("hypothesis_refs", "HYPOTHESIS"),),
            ),
            TypedRestoreProfile(
                "action-portfolio.v1",
                ActionPortfolioRecord,
                EntityType.ACTION,
                "portfolio_id",
                reference_fields=(("action_refs", "ACTION"),),
                display_name="행동 구성",
            ),
            TypedRestoreProfile(
                "action-plan.v1",
                ActionPlanRecord,
                EntityType.ACTION,
                "plan_id",
                reference_fields=(("selected_action_refs", "ACTION"),),
                display_name="행동 계획",
            ),
        )
    )
