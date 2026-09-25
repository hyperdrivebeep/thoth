from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, Field

from thoth.domain.action import ActionPlan
from thoth.domain.action_full import ActionPlanRecord, ActionRecord
from thoth.domain.base import DomainModel
from thoth.domain.criterion import CriterionCandidate
from thoth.domain.enums import ModelRole
from thoth.domain.evidence import EvidenceSpan, InformationSufficiencyAssessment
from thoth.domain.hypothesis import HypothesisPortfolio
from thoth.domain.hypothesis_full import HypothesisPortfolioRecord, HypothesisRecord
from thoth.domain.model_settings import ResolvedModelSettings
from thoth.domain.test_validity import TestValidityAssessment


class ContextPack(DomainModel):
    case_id: str
    project_id: str
    object_id: str
    problem: str
    evidence: tuple[EvidenceSpan, ...]
    criteria: tuple[CriterionCandidate, ...]
    sufficiency: InformationSufficiencyAssessment | None
    input_head_set_digest: str
    policy_hints: dict[str, object] = Field(default_factory=dict)
    previous_portfolio: HypothesisPortfolio | None = None
    previous_action_plan: ActionPlan | None = None
    candidate_portfolio: HypothesisPortfolio | None = None
    canonical_hypotheses: tuple[HypothesisRecord, ...] = ()
    canonical_portfolio: HypothesisPortfolioRecord | None = None
    canonical_actions: tuple[ActionRecord, ...] = ()
    canonical_action_plan: ActionPlanRecord | None = None
    canonical_test_assessments: tuple[TestValidityAssessment, ...] = ()
    unresolved_research_refs: tuple[str, ...] = ()
    research_context: dict[str, object] = Field(default_factory=dict)


@dataclass(frozen=True)
class ModelRequest[TModel: BaseModel]:
    role: ModelRole
    project_id: str
    cutoff_at: datetime
    context_pack: ContextPack
    output_model: type[TModel]
    prompt_version: str
    model_policy_ref: str
    max_output_tokens: int
    model_settings: ResolvedModelSettings | None = None


@dataclass(frozen=True)
class ModelResult[TModel: BaseModel]:
    output: TModel
    model_id: str
    prompt_version: str
    scripted: bool
    input_digest: str
    output_digest: str
    dispatch_ids: tuple[str, ...] = ()
