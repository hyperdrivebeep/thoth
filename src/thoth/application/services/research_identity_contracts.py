"""Shared immutable research context and staged ownership contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from thoth.domain.action import ActionPlan
from thoth.domain.action_full import ActionPlanRecord, ActionPortfolioRecord, ActionRecord
from thoth.domain.hypothesis import HypothesisPortfolio
from thoth.domain.hypothesis_full import HypothesisPortfolioRecord, HypothesisRecord
from thoth.domain.research_basis import BasisCurrentness
from thoth.domain.revision import StagedRevision
from thoth.domain.test_validity import TestValidityAssessment


@dataclass(frozen=True)
class ResearchContext:
    project_id: str
    object_id: str
    heads: Mapping[str, str]
    hypotheses: tuple[HypothesisRecord, ...] = ()
    portfolio: HypothesisPortfolioRecord | None = None
    portfolio_view: HypothesisPortfolio | None = None
    actions: tuple[ActionRecord, ...] = ()
    action_plan: ActionPlanRecord | None = None
    action_plan_view: ActionPlan | None = None
    action_portfolio: ActionPortfolioRecord | None = None
    action_portfolio_id: str | None = None
    unrepresented_refs: tuple[str, ...] = ()
    portfolio_id: str | None = None
    test_assessments: tuple[TestValidityAssessment, ...] = ()
    currentness: Mapping[str, BasisCurrentness] = field(default_factory=dict[str, BasisCurrentness])


@dataclass(frozen=True)
class HypothesisOwnershipBatch:
    hypotheses: tuple[HypothesisRecord, ...]
    portfolio: HypothesisPortfolioRecord
    staged: tuple[StagedRevision, ...]


@dataclass(frozen=True)
class ActionOwnershipBatch:
    actions: tuple[ActionRecord, ...]
    portfolio: ActionPortfolioRecord
    plan: ActionPlanRecord
    staged: tuple[StagedRevision, ...]


@dataclass(frozen=True)
class ResearchOwnershipBatch:
    hypotheses: HypothesisOwnershipBatch
    actions: ActionOwnershipBatch

    @property
    def additional_staged(self) -> tuple[StagedRevision, ...]:
        return (*self.hypotheses.staged[1:], *self.actions.staged[1:])
