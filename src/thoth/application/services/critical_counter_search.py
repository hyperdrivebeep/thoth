from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from thoth.application.services.research_identity_service import (
    ResearchContext,
    ResearchIdentityService,
)
from thoth.application.services.revision_service import (
    CommitDisposition,
    CommitResult,
    RevisionCommitService,
)
from thoth.domain.actor import ActorRef
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.counterevidence import (
    CounterReviewTerminal,
    CounterSearchPlan,
    CounterSearchTrack,
    HypothesisCriticalReview,
    IndependentGateReview,
)
from thoth.domain.enums import (
    ActorKind,
    AuthorityState,
    CutoffState,
    HypothesisStatus,
    PortfolioStatus,
    RiskTier,
)
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.evidence_graph import EvidenceSourceRecord
from thoth.domain.hypothesis import Hypothesis, HypothesisPortfolio
from thoth.domain.policy import AuthoritativeExecutionPolicy, CounterSearchRoute
from thoth.domain.project import Project, WorkThread
from thoth.domain.revision import (
    ImpactPropagationPlan,
    RevisionChangeSet,
)
from thoth.ports.ledger import LedgerPort
from thoth.ports.runtime import ClockPort, IdGeneratorPort

_RISK_ORDER = {
    RiskTier.R0: 0,
    RiskTier.R1: 1,
    RiskTier.R2: 2,
    RiskTier.R3: 3,
    RiskTier.R4: 4,
}
_FORBIDDEN_CONTEXT_TAGS = {
    "FUTURE",
    "ORACLE",
    "HIDDEN_HOLDOUT",
    "CROSS_PROJECT",
    "EXPIRED",
    "QUARANTINED",
    "SECRET",
}


@dataclass(frozen=True)
class CounterSearchBasis:
    portfolio_head: str
    head_set_digest: str
    research: ResearchContext

    @property
    def protected_heads(self) -> dict[str, str]:
        keys = {f"HYPOTHESIS:{item.hypothesis_id}" for item in self.research.hypotheses}
        keys.update(
            (
                f"HYPOTHESIS:{self.research.portfolio_id}",
                f"DECISION_OBJECT:{self.research.object_id}",
            )
        )
        return {key: value for key, value in self.research.heads.items() if key in keys}

    def matches(self, heads: Mapping[str, str]) -> bool:
        return all(heads.get(key) == digest for key, digest in self.protected_heads.items())


@dataclass(frozen=True)
class CriticalRevisionResult:
    portfolio: HypothesisPortfolio
    hypothesis_before: Hypothesis
    hypothesis_after: Hypothesis
    critical_review: HypothesisCriticalReview
    commit: CommitResult


class CounterevidenceChallenger:
    def __init__(self, *, clock: ClockPort, ids: IdGeneratorPort) -> None:
        self._clock = clock
        self._ids = ids

    def challenge(
        self,
        *,
        project: Project,
        thread: WorkThread,
        investigation_id: str,
        hypothesis: Hypothesis,
        route: CounterSearchRoute,
        policy: AuthoritativeExecutionPolicy,
    ) -> CounterSearchPlan:
        primary_query_family = next(iter(route.query_families), "")
        independent_track = CounterSearchTrack(
            track_id=self._ids.new("counter-search-track"),
            kind="INDEPENDENT_SOURCE",
            query_family=primary_query_family,
            source_territory=route.source_territory,
            alternative_explanation=route.alternative_explanation,
        )
        alternative_track = CounterSearchTrack(
            track_id=self._ids.new("counter-search-track"),
            kind="ALTERNATIVE_EXPLANATION",
            query_family=(
                route.query_families[1] if len(route.query_families) > 1 else primary_query_family
            ),
            source_territory=route.source_territory,
            alternative_explanation=route.alternative_explanation,
        )
        draft: dict[str, object] = {
            "plan_id": self._ids.new("counter-search-plan"),
            "project_id": project.project_id,
            "thread_id": thread.thread_id,
            "investigation_id": investigation_id,
            "hypothesis_id": hypothesis.hypothesis_id,
            "mode": "CRITICAL",
            "connector_id": route.connector_id,
            "selector": route.selector,
            "query_families": route.query_families,
            "tracks": (independent_track, alternative_track),
            "source_territory": route.source_territory,
            "independence_group": route.independence_group,
            "alternative_explanation_track": route.alternative_explanation,
            "source_authority": route.source_authority.value,
            "temporal_state": route.temporal_state.value,
            "support_match_terms": route.support_match_terms,
            "counter_match_terms": route.counter_match_terms,
            "context_tags": route.context_tags,
            "max_waves": route.max_waves,
            "max_results": route.max_results,
            "policy_id": policy.policy_id,
            "policy_revision": policy.policy_revision,
            "policy_digest": policy.policy_digest,
            "challenger_version": "counterevidence-challenger:1.0.0",
            "created_at": self._clock.now(),
            "route_id": route.route_id,
            "priority": route.priority,
            "depth": route.depth,
            "excursion": route.excursion,
            "checkpoint_after": route.checkpoint_after,
            "estimated_cost_microunits": route.estimated_cost_microunits,
        }
        return CounterSearchPlan.model_validate(
            {
                **draft,
                "plan_digest": domain_digest(
                    "COUNTER_SEARCH_PLAN", "1.0.0", canonical_payload(draft)
                ),
            }
        )


class IndependentGateReviewer:
    def __init__(self, *, clock: ClockPort, ids: IdGeneratorPort) -> None:
        self._clock = clock
        self._ids = ids

    def preflight(
        self,
        *,
        plan: CounterSearchPlan,
        primary_sources: tuple[EvidenceSourceRecord, ...],
    ) -> IndependentGateReview:
        forbidden = sorted(_FORBIDDEN_CONTEXT_TAGS & {item.upper() for item in plan.context_tags})
        if forbidden:
            return self._review(
                plan,
                terminal=CounterReviewTerminal.UNRESOLVED_PROHIBITED_CONTEXT,
                independence_state="NOT_REVIEWED",
                authority_state="NOT_REVIEWED",
                temporal_state="NOT_REVIEWED",
                context_state="PROHIBITED",
                relevance_state="NOT_REVIEWED",
                reasons=(f"forbidden context tags: {', '.join(forbidden)}",),
            )
        if plan.source_authority not in {
            AuthorityState.OFFICIAL.value,
            AuthorityState.APPROVED.value,
        }:
            return self._review(
                plan,
                terminal=CounterReviewTerminal.UNRESOLVED_AUTHORITY,
                independence_state="NOT_REVIEWED",
                authority_state="INSUFFICIENT",
                temporal_state="NOT_REVIEWED",
                context_state="ALLOWED",
                relevance_state="NOT_REVIEWED",
                reasons=("counter-search route lacks official or approved source authority",),
            )
        if plan.temporal_state != CutoffState.ELIGIBLE.value:
            return self._review(
                plan,
                terminal=CounterReviewTerminal.UNRESOLVED_TEMPORAL,
                independence_state="NOT_REVIEWED",
                authority_state="SUFFICIENT",
                temporal_state="INELIGIBLE",
                context_state="ALLOWED",
                relevance_state="NOT_REVIEWED",
                reasons=("counter-search route is not cutoff eligible",),
            )
        if not primary_sources:
            return self._review(
                plan,
                terminal=CounterReviewTerminal.UNRESOLVED_INDEPENDENCE,
                independence_state="UNVERIFIABLE",
                authority_state="SUFFICIENT",
                temporal_state="ELIGIBLE",
                context_state="ALLOWED",
                relevance_state="NOT_REVIEWED",
                reasons=("primary hypothesis source lineage is unavailable",),
            )
        primary_territories = {
            value
            for source in primary_sources
            for value in (source.connector_ref, source.lineage_root_id)
        }
        if (
            plan.connector_id in primary_territories
            or plan.source_territory in primary_territories
            or plan.independence_group in primary_territories
        ):
            return self._review(
                plan,
                terminal=CounterReviewTerminal.UNRESOLVED_INDEPENDENCE,
                independence_state="INSUFFICIENT",
                authority_state="SUFFICIENT",
                temporal_state="ELIGIBLE",
                context_state="ALLOWED",
                relevance_state="NOT_REVIEWED",
                reasons=("counter-search route overlaps primary source territory",),
            )
        return self._review(
            plan,
            terminal=CounterReviewTerminal.UNRESOLVED_NO_RESULTS,
            independence_state="PREFLIGHT_PASS",
            authority_state="SUFFICIENT",
            temporal_state="ELIGIBLE",
            context_state="ALLOWED",
            relevance_state="PENDING_RESULTS",
            reasons=("independent route is eligible for bounded execution",),
        )

    def review_results(
        self,
        *,
        plan: CounterSearchPlan,
        primary_sources: tuple[EvidenceSourceRecord, ...],
        acquired_source: EvidenceSourceRecord,
        acquired_spans: tuple[EvidenceSpan, ...],
    ) -> IndependentGateReview:
        preflight = self.preflight(plan=plan, primary_sources=primary_sources)
        if preflight.independence_state != "PREFLIGHT_PASS":
            return preflight
        if acquired_source.project_id != plan.project_id:
            return self._review(
                plan,
                terminal=CounterReviewTerminal.UNRESOLVED_PROHIBITED_CONTEXT,
                independence_state="CROSS_PROJECT",
                authority_state="NOT_REVIEWED",
                temporal_state="NOT_REVIEWED",
                context_state="PROHIBITED",
                relevance_state="NOT_REVIEWED",
                reasons=("acquired source belongs to another project",),
            )
        primary_lineages = {source.lineage_root_id for source in primary_sources}
        if acquired_source.lineage_root_id in primary_lineages or acquired_source.connector_ref in {
            source.connector_ref for source in primary_sources
        }:
            return self._review(
                plan,
                terminal=CounterReviewTerminal.UNRESOLVED_INDEPENDENCE,
                independence_state="INSUFFICIENT",
                authority_state="NOT_REVIEWED",
                temporal_state="NOT_REVIEWED",
                context_state="ALLOWED",
                relevance_state="NOT_REVIEWED",
                reasons=("acquired source is not independent from primary source lineage",),
            )
        if acquired_source.authority_status not in {
            AuthorityState.OFFICIAL,
            AuthorityState.APPROVED,
        }:
            return self._review(
                plan,
                terminal=CounterReviewTerminal.UNRESOLVED_AUTHORITY,
                independence_state="PASS",
                authority_state="INSUFFICIENT",
                temporal_state="NOT_REVIEWED",
                context_state="ALLOWED",
                relevance_state="NOT_REVIEWED",
                reasons=("acquired source authority is insufficient",),
            )
        if acquired_source.cutoff_eligibility != CutoffState.ELIGIBLE:
            return self._review(
                plan,
                terminal=CounterReviewTerminal.UNRESOLVED_TEMPORAL,
                independence_state="PASS",
                authority_state="SUFFICIENT",
                temporal_state="INELIGIBLE",
                context_state="ALLOWED",
                relevance_state="NOT_REVIEWED",
                reasons=("acquired source is not cutoff eligible",),
            )
        counter_refs = tuple(
            span.span_id
            for span in acquired_spans
            if any(term.lower() in span.exact_text.lower() for term in plan.counter_match_terms)
        )
        support_refs = tuple(
            span.span_id
            for span in acquired_spans
            if any(term.lower() in span.exact_text.lower() for term in plan.support_match_terms)
        )
        if counter_refs:
            return self._review(
                plan,
                terminal=CounterReviewTerminal.ELIMINATED_WITHIN_SCOPE,
                independence_state="PASS",
                authority_state="SUFFICIENT",
                temporal_state="ELIGIBLE",
                context_state="ALLOWED",
                relevance_state="COUNTEREVIDENCE_MATCH",
                reasons=("independent source matched the counterevidence contract",),
                accepted_evidence_refs=counter_refs,
                executed_track_ids=tuple(track.track_id for track in plan.tracks),
            )
        if support_refs:
            return self._review(
                plan,
                terminal=CounterReviewTerminal.SUPPORTED,
                independence_state="PASS",
                authority_state="SUFFICIENT",
                temporal_state="ELIGIBLE",
                context_state="ALLOWED",
                relevance_state="SUPPORT_MATCH",
                reasons=("independent source matched the support contract",),
                accepted_evidence_refs=support_refs,
                executed_track_ids=tuple(track.track_id for track in plan.tracks),
            )
        return self._review(
            plan,
            terminal=CounterReviewTerminal.UNRESOLVED_NO_RESULTS,
            independence_state="PASS",
            authority_state="SUFFICIENT",
            temporal_state="ELIGIBLE",
            context_state="ALLOWED",
            relevance_state="NO_MATCH",
            reasons=("independent search returned no contract-matching result",),
            executed_track_ids=tuple(track.track_id for track in plan.tracks),
        )

    def unresolved(
        self,
        plan: CounterSearchPlan,
        *,
        terminal: CounterReviewTerminal,
        reason: str,
    ) -> IndependentGateReview:
        return self._review(
            plan,
            terminal=terminal,
            independence_state="NOT_COMPLETED",
            authority_state="NOT_COMPLETED",
            temporal_state="NOT_COMPLETED",
            context_state="ALLOWED",
            relevance_state="NOT_COMPLETED",
            reasons=(reason,),
        )

    def aggregate(
        self,
        plan: CounterSearchPlan,
        *,
        terminal: CounterReviewTerminal,
        reasons: tuple[str, ...],
        accepted_evidence_refs: tuple[str, ...],
        executed_track_ids: tuple[str, ...],
    ) -> IndependentGateReview:
        return self._review(
            plan,
            terminal=terminal,
            independence_state="AGGREGATED",
            authority_state="AGGREGATED",
            temporal_state="AGGREGATED",
            context_state="ALLOWED",
            relevance_state="MULTI_WAVE_AGGREGATE",
            reasons=reasons,
            accepted_evidence_refs=accepted_evidence_refs,
            executed_track_ids=executed_track_ids,
        )

    def _review(
        self,
        plan: CounterSearchPlan,
        *,
        terminal: CounterReviewTerminal,
        independence_state: str,
        authority_state: str,
        temporal_state: str,
        context_state: str,
        relevance_state: str,
        reasons: tuple[str, ...],
        accepted_evidence_refs: tuple[str, ...] = (),
        executed_track_ids: tuple[str, ...] = (),
    ) -> IndependentGateReview:
        draft: dict[str, object] = {
            "review_id": self._ids.new("independent-gate-review"),
            "project_id": plan.project_id,
            "hypothesis_id": plan.hypothesis_id,
            "plan_id": plan.plan_id,
            "independence_state": independence_state,
            "authority_state": authority_state,
            "temporal_state": temporal_state,
            "context_state": context_state,
            "relevance_state": relevance_state,
            "terminal": terminal,
            "reasons": reasons,
            "accepted_evidence_refs": accepted_evidence_refs,
            "executed_track_ids": executed_track_ids,
            "reviewer_version": "independent-gate-reviewer:1.0.0",
            "reviewed_at": self._clock.now(),
        }
        return IndependentGateReview.model_validate(
            {
                **draft,
                "review_digest": domain_digest(
                    "INDEPENDENT_GATE_REVIEW", "1.0.0", canonical_payload(draft)
                ),
            }
        )


class CounterSearchRevisionService:
    def __init__(
        self,
        *,
        ledger: LedgerPort,
        research: ResearchIdentityService,
        clock: ClockPort,
        ids: IdGeneratorPort,
    ) -> None:
        self._ledger = ledger
        self._research = research
        self._clock = clock
        self._ids = ids

    def apply(
        self,
        *,
        project: Project,
        thread: WorkThread,
        portfolio: HypothesisPortfolio,
        hypothesis: Hypothesis,
        investigation_id: str,
        plan: CounterSearchPlan,
        gate: IndependentGateReview,
        basis: CounterSearchBasis,
    ) -> CriticalRevisionResult:
        support_added = (
            gate.accepted_evidence_refs if gate.terminal == CounterReviewTerminal.SUPPORTED else ()
        )
        counter_added = (
            gate.accepted_evidence_refs
            if gate.terminal == CounterReviewTerminal.ELIMINATED_WITHIN_SCOPE
            else ()
        )
        critical_draft: dict[str, object] = {
            "investigation_id": investigation_id,
            "plan_id": plan.plan_id,
            "gate_review_id": gate.review_id,
            "terminal": gate.terminal,
            "support_added_refs": support_added,
            "counterevidence_added_refs": counter_added,
            "reasons": gate.reasons,
            "semantic_truth_certified": False,
        }
        critical_review = HypothesisCriticalReview.model_validate(
            {
                **critical_draft,
                "review_digest": domain_digest(
                    "HYPOTHESIS_CRITICAL_REVIEW",
                    "1.0.0",
                    canonical_payload(critical_draft),
                ),
            }
        )
        status = hypothesis.status
        if gate.terminal == CounterReviewTerminal.SUPPORTED:
            status = HypothesisStatus.COUNTEREVIDENCE_CHECKED
        elif gate.terminal == CounterReviewTerminal.ELIMINATED_WITHIN_SCOPE:
            status = HypothesisStatus.ELIMINATED_WITHIN_SCOPE
        elif gate.terminal in {
            CounterReviewTerminal.UNRESOLVED_NO_RESULTS,
            CounterReviewTerminal.UNRESOLVED_INDEPENDENCE,
            CounterReviewTerminal.UNRESOLVED_AUTHORITY,
            CounterReviewTerminal.UNRESOLVED_TEMPORAL,
            CounterReviewTerminal.UNRESOLVED_PROHIBITED_CONTEXT,
        }:
            status = HypothesisStatus.INCONCLUSIVE
        revised_hypothesis = hypothesis.model_copy(
            update={
                "support_evidence_refs": tuple(
                    dict.fromkeys((*hypothesis.support_evidence_refs, *support_added))
                ),
                "counterevidence_refs": tuple(
                    dict.fromkeys((*hypothesis.counterevidence_refs, *counter_added))
                ),
                "status": status,
                "critical_review": critical_review,
            }
        )
        revised_portfolio = portfolio.model_copy(
            update={
                "hypotheses": tuple(
                    revised_hypothesis if item.hypothesis_id == hypothesis.hypothesis_id else item
                    for item in portfolio.hypotheses
                ),
                "status": PortfolioStatus.EMPIRICALLY_UPDATED,
                "generated_from_head_set": basis.head_set_digest,
            }
        )
        actor = ActorRef(
            actor_id="agent:independent-gate-reviewer",
            kind=ActorKind.AGENT,
            role="deterministic-independent-gate-reviewer",
        )
        batch = self._research.stage_hypotheses(
            revised_portfolio, actor=actor, context=basis.research
        )
        with self._ledger.transaction():
            commit = RevisionCommitService(
                self._ledger, self._clock, self._ids, policy_version=plan.policy_digest
            ).commit(
                RevisionChangeSet(
                    changeset_id=self._ids.new("changeset"),
                    project_id=project.project_id,
                    expected_heads=basis.protected_heads,
                    staged_revisions=batch.staged,
                    impact_plan=ImpactPropagationPlan(
                        recalculate_refs=(f"ACTION:thread:{thread.thread_id}",)
                    ),
                    actor=actor,
                    reason=f"critical counter-search {gate.terminal.value}",
                )
            )
            if commit.disposition == CommitDisposition.FAST_FORWARD:
                self._research.persist_hypotheses(batch)
        return CriticalRevisionResult(
            portfolio=revised_portfolio,
            hypothesis_before=hypothesis,
            hypothesis_after=revised_hypothesis,
            critical_review=critical_review,
            commit=commit,
        )


def select_leading_high_risk_hypothesis(
    portfolio: HypothesisPortfolio,
) -> Hypothesis | None:
    ranked = sorted(
        portfolio.hypotheses,
        key=lambda item: (
            len(item.support_evidence_refs),
            max((_RISK_ORDER[test.risk_tier] for test in item.discriminating_tests), default=0),
            item.hypothesis_id,
        ),
        reverse=True,
    )
    if not ranked:
        return None
    candidate = ranked[0]
    high_risk = any(
        _RISK_ORDER[test.risk_tier] >= _RISK_ORDER[RiskTier.R2]
        for test in candidate.discriminating_tests
    )
    contested = candidate.status == HypothesisStatus.INCONCLUSIVE
    return candidate if high_risk or contested else None
