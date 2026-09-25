"""Model input views distinguish historical proposals from currently reviewed judgments."""

from thoth.domain.action_full import ActionPlanRecord, ActionPortfolioRecord, ActionRecord
from thoth.domain.base import DomainModel
from thoth.domain.enums import ActionState, HypothesisStatus, PortfolioStatus
from thoth.domain.hypothesis_full import HypothesisPortfolioRecord, HypothesisRecord
from thoth.domain.research_basis import BasisCurrentness


def currentness_view(record: DomainModel, currentness: BasisCurrentness) -> DomainModel:
    if currentness.state == "CURRENT":
        return record
    updates: dict[str, object] = {}
    if "freshness" in type(record).model_fields:
        updates["freshness"] = "REVIEW_REQUIRED"
    if isinstance(record, HypothesisRecord):
        updates["empirical_appraisal"] = "UNASSESSED"
        if record.generation_details is not None:
            updates["generation_details"] = record.generation_details.model_copy(
                update={
                    "candidate_status": HypothesisStatus.DRAFT,
                    "execution_appraisal": None,
                    "critical_review": None,
                    "semantic_review_ref": None,
                }
            )
    elif isinstance(record, HypothesisPortfolioRecord):
        updates.update(coverage_state="NOT_ASSESSED", discrimination_state="NOT_ASSESSED")
        if record.generation_details is not None:
            updates["generation_details"] = record.generation_details.model_copy(
                update={"candidate_status": PortfolioStatus.DRAFT}
            )
    elif isinstance(record, ActionRecord):
        updates.update(decision_state="NOT_EVALUATED", authorization_state="NOT_EVALUATED")
        if record.generation_details is not None:
            updates["generation_details"] = record.generation_details.model_copy(
                update={
                    "candidate_state": ActionState.PROHIBITED
                    if record.risk_tier == "R4"
                    else ActionState.PROPOSED
                }
            )
    elif isinstance(record, ActionPortfolioRecord):
        updates.update(
            selected_action_ref=None, recommendation_ref=None, decision_state="NOT_EVALUATED"
        )
    elif isinstance(record, ActionPlanRecord):
        updates.update(
            authorization_refs=(), auto_executable_frontier=(), validation_state="REVIEW_REQUIRED"
        )
    return record.model_copy(update=updates)
