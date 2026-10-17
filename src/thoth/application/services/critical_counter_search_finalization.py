from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast

from pydantic import JsonValue

from thoth.application.services.counter_search_calculations import (
    aggregate_gate_inputs,
    resolve_loop_terminal,
)
from thoth.application.services.counter_search_preflight import CounterSearchBlocks
from thoth.application.services.critical_counter_search import (
    CounterSearchBasis,
    CounterSearchRevisionService,
    IndependentGateReviewer,
)
from thoth.application.services.investigation_service import InvestigationService
from thoth.application.services.research_identity_service import ResearchIdentityService
from thoth.application.services.revision_service import CommitDisposition
from thoth.domain.counterevidence import (
    CounterLoopTerminal,
    CounterReviewTerminal,
    CounterSearchBudgetState,
    CounterSearchPlan,
    CounterWaveRecord,
    IndependentGateReview,
)
from thoth.domain.evidence_graph import EvidenceSourceRecord
from thoth.domain.hypothesis import Hypothesis, HypothesisPortfolio
from thoth.domain.investigation import InvestigationRecord
from thoth.domain.project import Project, WorkThread


@dataclass
class IndependentConfirmationTracker:
    seen_groups: set[str] = field(default_factory=set)
    seen_lineages: set[str] = field(default_factory=set)
    counter_groups: set[str] = field(default_factory=set)
    support_groups: set[str] = field(default_factory=set)

    def can_contribute(
        self,
        plan: CounterSearchPlan,
        source: EvidenceSourceRecord,
        *,
        content_duplicate: bool,
    ) -> bool:
        return (
            not content_duplicate
            and plan.independence_group not in self.seen_groups
            and source.lineage_root_id not in self.seen_lineages
        )

    def record(
        self,
        plan: CounterSearchPlan,
        source: EvidenceSourceRecord,
        terminal: CounterReviewTerminal,
    ) -> None:
        self.seen_groups.add(plan.independence_group)
        self.seen_lineages.add(source.lineage_root_id)
        target = (
            self.counter_groups
            if terminal == CounterReviewTerminal.ELIMINATED_WITHIN_SCOPE
            else self.support_groups
        )
        target.add(plan.independence_group)

    @property
    def maximum(self) -> int:
        return max(len(self.counter_groups), len(self.support_groups))


class CounterSearchFinalizationService:
    """Bind counter-search revisions to the pre-I/O head and project safe current state."""

    def __init__(
        self,
        *,
        revisions: CounterSearchRevisionService,
        reviewer: IndependentGateReviewer,
        investigations: InvestigationService,
        research: ResearchIdentityService,
    ) -> None:
        self._revisions = revisions
        self._reviewer = reviewer
        self._investigations = investigations
        self._research = research

    def single(
        self,
        *,
        project: Project,
        thread: WorkThread,
        portfolio: HypothesisPortfolio,
        hypothesis: Hypothesis,
        investigation: InvestigationRecord,
        plan: CounterSearchPlan,
        gate: IndependentGateReview,
        basis: CounterSearchBasis,
        connector_run: dict[str, JsonValue] | None,
    ) -> dict[str, JsonValue]:
        revision = self._revisions.apply(
            project=project,
            thread=thread,
            portfolio=portfolio,
            hypothesis=hypothesis,
            investigation_id=investigation.investigation_id,
            plan=plan,
            gate=gate,
            basis=basis,
        )
        projected_portfolio = revision.portfolio
        projected_hypothesis = revision.hypothesis_after
        support_added = revision.critical_review.support_added_refs
        counter_added = revision.critical_review.counterevidence_added_refs
        if revision.commit.disposition == CommitDisposition.BRANCH:
            gate = self._reviewer.unresolved(
                plan,
                terminal=CounterReviewTerminal.UNRESOLVED_CONFLICT,
                reason="portfolio head changed during counter-search I/O",
            )
            projected_portfolio = self._current_portfolio(
                project.project_id,
                portfolio.portfolio_id,
            )
            projected_hypothesis = self._hypothesis(projected_portfolio, hypothesis.hypothesis_id)
            support_added = ()
            counter_added = ()
        self._investigations.audit(
            investigation,
            "investigation/independentGateReviewed",
            {
                "gate": gate.model_dump(mode="json"),
                "role": "INDEPENDENT_GATE_REVIEWER",
                "revision_ids": revision.commit.committed_revision_ids,
                "receipt_id": revision.commit.receipt.receipt_id,
            },
        )
        return {
            "terminal_state": gate.terminal.value,
            "investigation": cast(JsonValue, investigation.model_dump(mode="json")),
            "challenger_plan": cast(JsonValue, plan.model_dump(mode="json")),
            "gate_review": cast(JsonValue, gate.model_dump(mode="json")),
            "hypothesis_before": cast(
                JsonValue, revision.hypothesis_before.model_dump(mode="json")
            ),
            "hypothesis_after": cast(JsonValue, projected_hypothesis.model_dump(mode="json")),
            "support_diff": {"added": list(support_added)},
            "counterevidence_diff": {"added": list(counter_added)},
            "connector_run": connector_run,
            "non_truth_receipt": cast(JsonValue, revision.commit.receipt.model_dump(mode="json")),
            "portfolio_id": projected_portfolio.portfolio_id,
            "portfolio": cast(JsonValue, projected_portfolio.model_dump(mode="json")),
            "branch_revision_ids": list(revision.commit.branch_revision_ids),
        }

    def multi(
        self,
        *,
        project: Project,
        thread: WorkThread,
        portfolio: HypothesisPortfolio,
        hypothesis: Hypothesis,
        investigation: InvestigationRecord,
        plans: list[CounterSearchPlan],
        gate: IndependentGateReview,
        loop_terminal: CounterLoopTerminal,
        basis: CounterSearchBasis,
        waves: list[CounterWaveRecord],
        budget: CounterSearchBudgetState,
        connector_run: dict[str, JsonValue] | None,
    ) -> dict[str, JsonValue]:
        revision = self._revisions.apply(
            project=project,
            thread=thread,
            portfolio=portfolio,
            hypothesis=hypothesis,
            investigation_id=investigation.investigation_id,
            plan=plans[0],
            gate=gate,
            basis=basis,
        )
        projected_portfolio = revision.portfolio
        projected_hypothesis = revision.hypothesis_after
        support_added = revision.critical_review.support_added_refs
        counter_added = revision.critical_review.counterevidence_added_refs
        if revision.commit.disposition == CommitDisposition.BRANCH:
            loop_terminal = CounterLoopTerminal.ABSTAINED
            gate = self._reviewer.unresolved(
                plans[0],
                terminal=CounterReviewTerminal.UNRESOLVED_CONFLICT,
                reason="portfolio head changed during multi-wave counter-search I/O",
            )
            projected_portfolio = self._current_portfolio(
                project.project_id,
                portfolio.portfolio_id,
            )
            projected_hypothesis = self._hypothesis(projected_portfolio, hypothesis.hypothesis_id)
            support_added = ()
            counter_added = ()
        self._investigations.audit(
            investigation,
            "investigation/multiWaveConverged",
            {
                "loop_terminal": loop_terminal.value,
                "budget": budget.model_dump(mode="json"),
                "receipt_id": revision.commit.receipt.receipt_id,
            },
        )
        return {
            "terminal_state": gate.terminal.value,
            "loop_terminal": loop_terminal.value,
            "investigation": cast(JsonValue, investigation.model_dump(mode="json")),
            "challenger_plan": cast(JsonValue, plans[0].model_dump(mode="json")),
            "challenger_plans": [plan.model_dump(mode="json") for plan in plans],
            "gate_review": cast(JsonValue, gate.model_dump(mode="json")),
            "waves": [wave.model_dump(mode="json") for wave in waves],
            "budget": cast(JsonValue, budget.model_dump(mode="json")),
            "hypothesis_before": cast(
                JsonValue, revision.hypothesis_before.model_dump(mode="json")
            ),
            "hypothesis_after": cast(JsonValue, projected_hypothesis.model_dump(mode="json")),
            "support_diff": {"added": list(support_added)},
            "counterevidence_diff": {"added": list(counter_added)},
            "connector_run": connector_run,
            "non_truth_receipt": cast(JsonValue, revision.commit.receipt.model_dump(mode="json")),
            "portfolio_id": projected_portfolio.portfolio_id,
            "portfolio": cast(JsonValue, projected_portfolio.model_dump(mode="json")),
            "branch_revision_ids": list(revision.commit.branch_revision_ids),
        }

    def _current_portfolio(self, project_id: str, portfolio_id: str) -> HypothesisPortfolio:
        return self._research.read_portfolio(project_id, portfolio_id)

    @staticmethod
    def _hypothesis(portfolio: HypothesisPortfolio, hypothesis_id: str) -> Hypothesis:
        return next(item for item in portfolio.hypotheses if item.hypothesis_id == hypothesis_id)


@dataclass(frozen=True)
class CounterLoopConclusion:
    terminal: CounterLoopTerminal
    gate: IndependentGateReview
    investigation: InvestigationRecord


def conclude_counter_loop(
    *,
    loop_terminal: CounterLoopTerminal | None,
    budget_exhausted: bool,
    counter_refs: list[str],
    support_refs: list[str],
    blocks: CounterSearchBlocks,
    waves: list[CounterWaveRecord],
    plans: list[CounterSearchPlan],
    confirmations: IndependentConfirmationTracker,
    executed_track_ids: list[str],
    investigation: InvestigationRecord,
    reviewer: IndependentGateReviewer,
    investigations: InvestigationService,
) -> CounterLoopConclusion:
    loop_terminal = resolve_loop_terminal(
        loop_terminal=loop_terminal,
        budget_exhausted=budget_exhausted,
        counter_refs=counter_refs,
        support_refs=support_refs,
        policy_blocks=blocks.policy,
        authority_blocks=blocks.authority,
        abstention_blocks=blocks.abstention,
        waves=waves,
    )
    if not plans:
        raise ValueError("multi-wave counter-search produced no challenger plan")
    gate_terminal, accepted_refs = aggregate_gate_inputs(loop_terminal, counter_refs, support_refs)
    gate = reviewer.aggregate(
        plans[0],
        terminal=gate_terminal,
        reasons=(
            f"multi-wave terminal: {loop_terminal.value}",
            f"counter confirmations: {len(confirmations.counter_groups)}",
            f"support confirmations: {len(confirmations.support_groups)}",
        ),
        accepted_evidence_refs=accepted_refs,
        executed_track_ids=tuple(dict.fromkeys(executed_track_ids)),
    )
    stopped = investigations.stop(
        investigation,
        reason=counter_loop_stop_reason(loop_terminal),
    )
    return CounterLoopConclusion(loop_terminal, gate, stopped)


def counter_loop_stop_reason(terminal: CounterLoopTerminal) -> str:
    if terminal in {
        CounterLoopTerminal.SUPPORTED,
        CounterLoopTerminal.ELIMINATED_WITHIN_SCOPE,
    }:
        return "SUFFICIENT"
    if terminal == CounterLoopTerminal.SEARCH_SATURATED:
        return "SEARCH_SATURATED"
    if terminal == CounterLoopTerminal.BUDGET_EXHAUSTED:
        return "BUDGET_EXHAUSTED"
    if terminal == CounterLoopTerminal.POLICY_BLOCKED:
        return "PERMISSION_BLOCKED"
    if terminal == CounterLoopTerminal.AUTHORITY_REQUIRED:
        return "PERMISSION_BLOCKED"
    return "HIGH_RISK_CONFLICT"
