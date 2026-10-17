from __future__ import annotations

from dataclasses import dataclass
from typing import Unpack, cast

from pydantic import JsonValue

from thoth.application.services.connector_service import ConnectorService
from thoth.application.services.counter_search_preflight import (
    CounterSearchBlock,
    CounterSearchBlocks,
    classify_counter_route,
)
from thoth.application.services.counter_search_preparation import (
    multi_wave_connector_request,
    multi_wave_investigation_spec,
)
from thoth.application.services.counter_search_publication import (
    CounterEvidenceArguments,
    build_counter_search_intent,
    publish_counter_evidence,
)
from thoth.application.services.counter_search_usage import CounterSearchUsage
from thoth.application.services.counter_wave_evaluation import CounterWaveEvaluator
from thoth.application.services.critical_counter_search import (
    CounterevidenceChallenger,
    CounterSearchBasis,
    CounterSearchRevisionService,
    IndependentGateReviewer,
    select_leading_high_risk_hypothesis,
)
from thoth.application.services.critical_counter_search_finalization import (
    CounterSearchFinalizationService,
    IndependentConfirmationTracker,
    conclude_counter_loop,
)
from thoth.application.services.evidence_graph_service import EvidenceGraphService
from thoth.application.services.investigation_service import InvestigationService
from thoth.application.services.research_identity_service import ResearchIdentityService
from thoth.domain.acquisition import SearchIntent
from thoth.domain.canonical import head_set_digest
from thoth.domain.connectors import ConnectorAccessRequest, ConnectorFailure
from thoth.domain.counterevidence import (
    CounterLoopTerminal,
    CounterReviewTerminal,
    CounterSearchPlan,
    CounterWaveRecord,
)
from thoth.domain.enums import AuthorityState, CutoffState, SecurityClass
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.evidence_graph import EvidenceSourceRecord
from thoth.domain.hypothesis import Hypothesis, HypothesisPortfolio
from thoth.domain.policy import AuthoritativeExecutionPolicy, CounterSearchRoute
from thoth.domain.project import Project, WorkThread
from thoth.ports.acquisition import AcquisitionTraceStorePort, EvidenceUnitOfWorkPort
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.evidence_graph import EvidenceGraphStorePort
from thoth.ports.governance import GovernanceStorePort, ProjectPolicyReaderPort
from thoth.ports.ledger import LedgerPort
from thoth.ports.runtime import ClockPort, IdGeneratorPort


@dataclass(frozen=True)
class CriticalCounterSearchExecution:
    projection: dict[str, JsonValue]


class CriticalCounterSearchCoordinator:
    def __init__(
        self,
        *,
        connectors: ConnectorService,
        evidence_graph: EvidenceGraphService,
        evidence_unit_of_work: EvidenceUnitOfWorkPort,
        evidence_store: EvidenceGraphStorePort,
        artifacts: ArtifactLedgerPort,
        investigations: InvestigationService,
        traces: AcquisitionTraceStorePort,
        governance: GovernanceStorePort,
        policies: ProjectPolicyReaderPort,
        ledger: LedgerPort,
        research: ResearchIdentityService,
        clock: ClockPort,
        ids: IdGeneratorPort,
    ) -> None:
        self._connectors = connectors
        self._evidence_graph = evidence_graph
        self._evidence_unit_of_work = evidence_unit_of_work
        self._evidence_store = evidence_store
        self._artifacts = artifacts
        self._investigations = investigations
        self._traces = traces
        self._governance = governance
        self._policies = policies
        self._ledger = ledger
        self._research = research
        self._clock = clock
        self._ids = ids

        def commit_evidence_candidate(**arguments: Unpack[CounterEvidenceArguments]) -> None:
            self._commit_evidence_candidate(**arguments)

        self._wave_evaluator = CounterWaveEvaluator(
            governance=governance,
            investigations=investigations,
            clock=clock,
            commit_evidence_candidate=commit_evidence_candidate,
        )
        self._challenger = CounterevidenceChallenger(clock=clock, ids=ids)
        self._reviewer = IndependentGateReviewer(clock=clock, ids=ids)
        revisions = CounterSearchRevisionService(
            ledger=ledger, research=research, clock=clock, ids=ids
        )
        self._finalizer = CounterSearchFinalizationService(
            revisions=revisions,
            reviewer=self._reviewer,
            investigations=investigations,
            research=research,
        )

    async def execute_if_required(
        self,
        *,
        project: Project,
        thread: WorkThread,
        portfolio: HypothesisPortfolio,
    ) -> CriticalCounterSearchExecution | None:
        leading = select_leading_high_risk_hypothesis(portfolio)
        if leading is None:
            return None
        context = self._counter_context(project, portfolio, leading)
        if context is None:
            return None
        policy, routes, basis = context
        if self._requires_multi_wave(policy, routes):
            return await self._execute_multi_wave(
                project=project,
                thread=thread,
                portfolio=portfolio,
                leading=leading,
                policy=policy,
                routes=routes,
                basis=basis,
            )
        route = next(iter(routes))
        investigation = self._investigations.start(
            thread=thread,
            trigger="HIGH_RISK_OR_CONTESTED_HYPOTHESIS",
            question=f"Challenge leading hypothesis {leading.hypothesis_id}",
            target_object_id=leading.object_id,
            target_hypothesis_id=leading.hypothesis_id,
            mode="CRITICAL",
            scope={
                **thread.scope,
                "cutoff": project.cutoff_at.isoformat(),
                "access": "PROJECT_POLICY_BOUND",
                "egress": "PROJECT_POLICY_BOUND",
                "security": policy.max_source_security_class.value,
                "source_territory": route.source_territory,
            },
            required_evidence_groups=(f"counterevidence:{leading.hypothesis_id}",),
            query_families=route.query_families,
            budget=route.max_waves,
            stop_conditions=(
                "INDEPENDENT_GATE_TERMINAL",
                "SEARCH_SATURATED",
                "POLICY_BLOCKED",
                "BUDGET_EXHAUSTED",
            ),
        )
        plan = self._challenger.challenge(
            project=project,
            thread=thread,
            investigation_id=investigation.investigation_id,
            hypothesis=leading,
            route=route,
            policy=policy,
        )
        intent = self._intent(plan)
        self._traces.put_search_intent(intent)
        self._investigations.audit(
            investigation,
            "investigation/counterSearchPlanned",
            {
                "plan": plan.model_dump(mode="json"),
                "role": "COUNTEREVIDENCE_CHALLENGER",
            },
        )
        primary_sources = self._primary_sources(project.project_id, leading.support_evidence_refs)
        gate = self._reviewer.preflight(plan=plan, primary_sources=primary_sources)
        acquired_spans: tuple[EvidenceSpan, ...] = ()
        acquired_source: EvidenceSourceRecord | None = None
        binding_id: str | None = None
        connector_run: dict[str, JsonValue] | None = None
        wave_executed = False
        if gate.relevance_state == "PENDING_RESULTS":
            try:
                acquisition = await self._connectors.acquire_one(
                    ConnectorAccessRequest(
                        actor_id="agent:critical-counter-search-coordinator",
                        project_id=project.project_id,
                        connector_id=plan.connector_id,
                        selector=plan.selector,
                        authority=AuthorityState(plan.source_authority),
                        cutoff_state=CutoffState(plan.temporal_state),
                        security_class=SecurityClass.INTERNAL,
                        cutoff_at=project.cutoff_at,
                        policy_id=policy.policy_id,
                        policy_revision=policy.policy_revision,
                        policy_digest=policy.policy_digest,
                    )
                )
                wave_executed = True
                acquired_spans = acquisition.ingestion.evidence_candidates[: plan.max_results]
                acquired_source = acquisition.source
                binding_id = acquisition.binding.binding_id
                connector_run = cast(dict[str, JsonValue], acquisition.run.model_dump(mode="json"))
                gate = self._reviewer.review_results(
                    plan=plan,
                    primary_sources=primary_sources,
                    acquired_source=acquired_source,
                    acquired_spans=acquired_spans,
                )
            except ConnectorFailure as exc:
                gate = self._reviewer.unresolved(
                    plan,
                    terminal=(
                        CounterReviewTerminal.UNRESOLVED_POLICY_BLOCKED
                        if exc.policy_denial is not None
                        else CounterReviewTerminal.UNRESOLVED_FAILED
                    ),
                    reason=str(exc),
                )
        if gate.terminal in {
            CounterReviewTerminal.SUPPORTED,
            CounterReviewTerminal.ELIMINATED_WITHIN_SCOPE,
        }:
            if not basis.matches(self._ledger.read_heads(project.project_id)):
                gate = self._reviewer.unresolved(
                    plan,
                    terminal=CounterReviewTerminal.UNRESOLVED_CONFLICT,
                    reason="portfolio head changed before evidence commit",
                )
            else:
                assert acquired_source is not None
                accepted = tuple(
                    span for span in acquired_spans if span.span_id in gate.accepted_evidence_refs
                )
                try:
                    self._commit_evidence_candidate(
                        project_id=project.project_id,
                        thread=thread,
                        plan=plan,
                        intent=intent,
                        source=acquired_source,
                        spans=accepted,
                        terminal=gate.terminal,
                    )
                except Exception as exc:
                    gate = self._reviewer.unresolved(
                        plan,
                        terminal=CounterReviewTerminal.UNRESOLVED_FAILED,
                        reason=f"counterevidence gate commit failed: {type(exc).__name__}",
                    )
        if (
            gate.terminal
            not in {
                CounterReviewTerminal.SUPPORTED,
                CounterReviewTerminal.ELIMINATED_WITHIN_SCOPE,
            }
            and binding_id is not None
        ):
            self._governance.set_source_binding_state(
                project.project_id,
                binding_id,
                state="DETACHED",
                updated_at=self._clock.now().isoformat(),
            )
        if wave_executed:
            investigation = self._investigations.record_wave(
                investigation,
                observation_count=(1 if gate.accepted_evidence_refs else 0),
                lead_count=(1 if gate.accepted_evidence_refs else 0),
                claim_candidate_count=(1 if gate.accepted_evidence_refs else 0),
                remaining_target_gaps=(
                    ()
                    if gate.terminal
                    in {
                        CounterReviewTerminal.SUPPORTED,
                        CounterReviewTerminal.ELIMINATED_WITHIN_SCOPE,
                    }
                    else (f"counterevidence:{leading.hypothesis_id}",)
                ),
            )
        stopped = self._investigations.stop(
            investigation,
            reason=self._stop_reason(gate.terminal),
        )
        return CriticalCounterSearchExecution(
            projection=self._finalizer.single(
                project=project,
                thread=thread,
                portfolio=portfolio,
                hypothesis=leading,
                investigation=stopped,
                plan=plan,
                gate=gate,
                basis=basis,
                connector_run=connector_run,
            )
        )

    @staticmethod
    def _requires_multi_wave(
        policy: AuthoritativeExecutionPolicy,
        routes: tuple[CounterSearchRoute, ...],
    ) -> bool:
        limits = policy.counter_search_limits
        return len(routes) > 1 or limits.max_waves > 1 or limits.min_independent_confirmations > 1

    def _counter_context(
        self,
        project: Project,
        portfolio: HypothesisPortfolio,
        hypothesis: Hypothesis,
    ) -> (
        tuple[
            AuthoritativeExecutionPolicy,
            tuple[CounterSearchRoute, ...],
            CounterSearchBasis,
        ]
        | None
    ):
        stored_policy = self._policies.read_policy(project.project_id)
        if stored_policy is None:
            return None
        policy = AuthoritativeExecutionPolicy.from_project_policy(stored_policy)
        routes = tuple(
            item
            for item in policy.counter_search_routes
            if hypothesis.primary_locus is not None
            and hypothesis.primary_locus.value in item.target_loci
        )
        heads = dict(self._ledger.read_heads(project.project_id))
        expected = heads.get(f"HYPOTHESIS:{portfolio.portfolio_id}")
        if not routes or expected is None:
            return None
        return (
            policy,
            routes,
            CounterSearchBasis(
                expected,
                head_set_digest(heads),
                self._research.context(project.project_id, portfolio.object_id, heads),
            ),
        )

    async def _execute_multi_wave(
        self,
        *,
        project: Project,
        thread: WorkThread,
        portfolio: HypothesisPortfolio,
        leading: Hypothesis,
        policy: AuthoritativeExecutionPolicy,
        routes: tuple[CounterSearchRoute, ...],
        basis: CounterSearchBasis,
    ) -> CriticalCounterSearchExecution:
        hypothesis = leading
        limits = policy.counter_search_limits
        ordered = tuple(sorted(routes, key=lambda item: (-item.priority, item.route_id)))
        investigation = self._investigations.start(
            **multi_wave_investigation_spec(project, thread, hypothesis, policy, ordered, limits)
        )
        primary_sources = self._primary_sources(
            project.project_id,
            hypothesis.support_evidence_refs,
        )
        usage = CounterSearchUsage(started_at=self._clock.now())
        decision_rank = 0
        low_voi_streak = 0
        blocks = CounterSearchBlocks()
        seen_content_digests: set[str] = set()
        confirmations = IndependentConfirmationTracker()
        counter_refs: list[str] = []
        support_refs: list[str] = []
        executed_track_ids: list[str] = []
        waves: list[CounterWaveRecord] = []
        plans: list[CounterSearchPlan] = []
        last_connector_run: dict[str, JsonValue] | None = None
        budget_exhausted = False
        loop_terminal: CounterLoopTerminal | None = None

        for route in ordered:
            now = self._clock.now()
            if usage.exhausted(limits, route.estimated_cost_microunits, now):
                budget_exhausted = True
                break
            plan = self._challenger.challenge(
                project=project,
                thread=thread,
                investigation_id=investigation.investigation_id,
                hypothesis=hypothesis,
                route=route,
                policy=policy,
            )
            plans.append(plan)
            intent = self._intent(plan)
            self._traces.put_search_intent(intent)
            self._investigations.audit(
                investigation,
                "investigation/counterSearchPlanned",
                {"plan": plan.model_dump(mode="json"), "role": "COUNTEREVIDENCE_CHALLENGER"},
            )
            block = classify_counter_route(route, plan, policy, primary_sources, self._reviewer)
            if block is not None:
                blocks.record(block)
                continue
            remaining_bytes, remaining_results, depleted = usage.remaining(limits)
            if depleted:
                budget_exhausted = True
                break
            try:
                acquisition = await self._connectors.acquire_one(
                    multi_wave_connector_request(project, plan, policy, remaining_bytes)
                )
            except ConnectorFailure as exc:
                blocks.record_failure(exc)
                continue
            acquired_spans = usage.record_success(
                acquisition, plan.estimated_cost_microunits, remaining_results
            )
            last_connector_run = cast(dict[str, JsonValue], acquisition.run.model_dump(mode="json"))
            gate = self._reviewer.review_results(
                plan=plan,
                primary_sources=primary_sources,
                acquired_source=acquisition.source,
                acquired_spans=acquired_spans,
            )
            if self._detach_on_head_change(
                project.project_id,
                basis,
                acquisition.binding.binding_id,
            ):
                loop_terminal = CounterLoopTerminal.ABSTAINED
                blocks.record(CounterSearchBlock.ABSTENTION)
                break
            outcome = self._wave_evaluator.evaluate(
                project_id=project.project_id,
                thread=thread,
                plan=plan,
                intent=intent,
                acquisition=acquisition,
                acquired_spans=acquired_spans,
                gate=gate,
                investigation=investigation,
                hypothesis_id=hypothesis.hypothesis_id,
                limits=limits,
                wave_index=usage.used_waves,
                seen_content_digests=seen_content_digests,
                confirmations=confirmations,
                counter_refs=counter_refs,
                support_refs=support_refs,
                executed_track_ids=executed_track_ids,
                decision_rank=decision_rank,
                low_voi_streak=low_voi_streak,
            )
            investigation = outcome.investigation
            decision_rank = outcome.decision_rank
            low_voi_streak = outcome.low_voi_streak
            wave = outcome.wave
            waves.append(wave)
            self._investigations.audit(
                investigation,
                "investigation/multiWaveCompleted",
                {"wave": wave.model_dump(mode="json")},
            )
            if len(confirmations.counter_groups) >= limits.min_independent_confirmations:
                loop_terminal = CounterLoopTerminal.ELIMINATED_WITHIN_SCOPE
                break
            if len(confirmations.support_groups) >= limits.min_independent_confirmations:
                loop_terminal = CounterLoopTerminal.SUPPORTED
                break
            if low_voi_streak >= 2:
                loop_terminal = CounterLoopTerminal.SEARCH_SATURATED
                break
        conclusion = conclude_counter_loop(
            loop_terminal=loop_terminal,
            budget_exhausted=budget_exhausted,
            counter_refs=counter_refs,
            support_refs=support_refs,
            blocks=blocks,
            waves=waves,
            plans=plans,
            confirmations=confirmations,
            executed_track_ids=executed_track_ids,
            investigation=investigation,
            reviewer=self._reviewer,
            investigations=self._investigations,
        )
        budget = usage.snapshot(limits, self._clock.now())
        return CriticalCounterSearchExecution(
            projection=self._finalizer.multi(
                project=project,
                thread=thread,
                portfolio=portfolio,
                hypothesis=hypothesis,
                investigation=conclusion.investigation,
                plans=plans,
                gate=conclusion.gate,
                loop_terminal=conclusion.terminal,
                basis=basis,
                waves=waves,
                budget=budget,
                connector_run=last_connector_run,
            )
        )

    def _detach_on_head_change(
        self,
        project_id: str,
        basis: CounterSearchBasis,
        binding_id: str,
    ) -> bool:
        if basis.matches(self._ledger.read_heads(project_id)):
            return False
        self._governance.set_source_binding_state(
            project_id,
            binding_id,
            state="DETACHED",
            updated_at=self._clock.now().isoformat(),
        )
        return True

    def _primary_sources(
        self,
        project_id: str,
        evidence_refs: tuple[str, ...],
    ) -> tuple[EvidenceSourceRecord, ...]:
        sources: dict[str, EvidenceSourceRecord] = {}
        for span_id in evidence_refs:
            span = self._artifacts.read_evidence(span_id)
            if span is None or span.project_id != project_id:
                continue
            source = self._evidence_store.read_source_by_artifact(span.artifact_id)
            if source is not None and source.project_id == project_id:
                sources[source.source_id] = source
        return tuple(sources[key] for key in sorted(sources))

    def _intent(self, plan: CounterSearchPlan) -> SearchIntent:
        return build_counter_search_intent(plan, clock=self._clock, ids=self._ids)

    def _commit_evidence_candidate(self, **arguments: Unpack[CounterEvidenceArguments]) -> None:
        publish_counter_evidence(
            **arguments,
            evidence_graph=self._evidence_graph,
            evidence_unit_of_work=self._evidence_unit_of_work,
            clock=self._clock,
            ids=self._ids,
        )

    @staticmethod
    def _stop_reason(terminal: CounterReviewTerminal) -> str:
        if terminal in {
            CounterReviewTerminal.SUPPORTED,
            CounterReviewTerminal.ELIMINATED_WITHIN_SCOPE,
        }:
            return "SUFFICIENT"
        if terminal == CounterReviewTerminal.UNRESOLVED_NO_RESULTS:
            return "SEARCH_SATURATED"
        if terminal in {
            CounterReviewTerminal.UNRESOLVED_POLICY_BLOCKED,
            CounterReviewTerminal.UNRESOLVED_AUTHORITY,
        }:
            return "PERMISSION_BLOCKED"
        if terminal == CounterReviewTerminal.UNRESOLVED_FAILED:
            return "CONNECTOR_FAILED"
        return "HIGH_RISK_CONFLICT"
