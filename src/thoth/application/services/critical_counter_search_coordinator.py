from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import cast

from pydantic import JsonValue

from thoth.application.services.connector_service import ConnectorService
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
)
from thoth.application.services.evidence_graph_service import EvidenceGraphService
from thoth.application.services.investigation_service import InvestigationService
from thoth.application.services.research_identity_service import ResearchIdentityService
from thoth.domain.acquisition import AcquisitionLead, SearchIntent
from thoth.domain.canonical import canonical_payload, domain_digest, head_set_digest
from thoth.domain.connectors import ConnectorAccessRequest, ConnectorFailure
from thoth.domain.counterevidence import (
    CounterLoopTerminal,
    CounterReviewTerminal,
    CounterSearchBudgetState,
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
            thread=thread,
            trigger="HIGH_RISK_MULTI_WAVE_COUNTER_SEARCH",
            question=f"Run bounded multi-wave challenge for {hypothesis.hypothesis_id}",
            target_object_id=hypothesis.object_id,
            target_hypothesis_id=hypothesis.hypothesis_id,
            mode="CRITICAL",
            scope={
                **thread.scope,
                "cutoff": project.cutoff_at.isoformat(),
                "access": "PROJECT_POLICY_BOUND",
                "egress": "PROJECT_POLICY_BOUND",
                "security": policy.max_source_security_class.value,
                "max_depth": str(limits.max_depth),
            },
            required_evidence_groups=(f"counterevidence:{hypothesis.hypothesis_id}",),
            query_families=tuple(
                dict.fromkeys(family for route in ordered for family in route.query_families)
            ),
            budget=limits.max_waves,
            stop_conditions=(
                "INDEPENDENT_CONFIRMATION_CONVERGED",
                "SEARCH_SATURATED",
                "BUDGET_EXHAUSTED",
                "POLICY_BLOCKED",
                "AUTHORITY_REQUIRED",
                "ABSTAINED",
            ),
        )
        primary_sources = self._primary_sources(
            project.project_id,
            hypothesis.support_evidence_refs,
        )
        started_at = self._clock.now()
        used_waves = 0
        used_results = 0
        used_documents = 0
        used_bytes = 0
        used_tool_calls = 0
        used_cost = 0
        decision_rank = 0
        low_voi_streak = 0
        policy_blocks = 0
        authority_blocks = 0
        abstention_blocks = 0
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
            elapsed = max(0, int((self._clock.now() - started_at).total_seconds()))
            if (
                used_waves >= limits.max_waves
                or used_documents >= limits.max_documents
                or used_results >= limits.max_results
                or used_bytes >= limits.max_bytes
                or used_tool_calls + 2 > limits.max_tool_calls
                or used_cost + route.estimated_cost_microunits > limits.max_cost_microunits
                or elapsed >= limits.max_time_seconds
            ):
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
            if route.depth > limits.max_depth:
                abstention_blocks += 1
                continue
            gate = self._reviewer.preflight(plan=plan, primary_sources=primary_sources)
            if gate.terminal == CounterReviewTerminal.UNRESOLVED_AUTHORITY:
                authority_blocks += 1
                continue
            if gate.terminal in {
                CounterReviewTerminal.UNRESOLVED_PROHIBITED_CONTEXT,
                CounterReviewTerminal.UNRESOLVED_TEMPORAL,
                CounterReviewTerminal.UNRESOLVED_INDEPENDENCE,
            }:
                abstention_blocks += 1
                continue
            if plan.connector_id not in policy.connector_allowlist:
                policy_blocks += 1
                continue
            remaining_bytes = limits.max_bytes - used_bytes
            remaining_results = limits.max_results - used_results
            if remaining_bytes <= 0 or remaining_results <= 0:
                budget_exhausted = True
                break
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
                        max_bytes=remaining_bytes,
                        policy_id=policy.policy_id,
                        policy_revision=policy.policy_revision,
                        policy_digest=policy.policy_digest,
                    )
                )
            except ConnectorFailure as exc:
                if exc.policy_denial is not None:
                    policy_blocks += 1
                else:
                    abstention_blocks += 1
                continue
            used_waves += 1
            used_documents += 1
            used_tool_calls += 2
            used_cost += plan.estimated_cost_microunits
            used_bytes += acquisition.receipt.byte_size
            acquired_spans = acquisition.ingestion.evidence_candidates[:remaining_results]
            used_results += len(acquired_spans)
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
                abstention_blocks += 1
                break
            content_digest = acquisition.ingestion.object_digest
            deduplicated = content_digest in seen_content_digests
            if not deduplicated:
                seen_content_digests.add(content_digest)
            contributes_confirmation = confirmations.can_contribute(
                plan,
                acquisition.source,
                content_duplicate=deduplicated,
            )
            rank_before = decision_rank
            accepted = tuple(
                span for span in acquired_spans if span.span_id in gate.accepted_evidence_refs
            )
            relevant = gate.terminal in {
                CounterReviewTerminal.SUPPORTED,
                CounterReviewTerminal.ELIMINATED_WITHIN_SCOPE,
            }
            computed_voi = (
                Decimal(plan.priority) / Decimal(100) / Decimal(1 + plan.estimated_cost_microunits)
                if relevant and contributes_confirmation
                else Decimal(0)
            )
            if relevant and not deduplicated:
                try:
                    self._commit_evidence_candidate(
                        project_id=project.project_id,
                        thread=thread,
                        plan=plan,
                        intent=intent,
                        source=acquisition.source,
                        spans=accepted,
                        terminal=gate.terminal,
                    )
                    if gate.terminal == CounterReviewTerminal.ELIMINATED_WITHIN_SCOPE:
                        counter_refs.extend(gate.accepted_evidence_refs)
                        if contributes_confirmation:
                            decision_rank -= 1
                    else:
                        support_refs.extend(gate.accepted_evidence_refs)
                        if contributes_confirmation:
                            decision_rank += 1
                    if contributes_confirmation:
                        confirmations.record(plan, acquisition.source, gate.terminal)
                    executed_track_ids.extend(gate.executed_track_ids)
                except Exception:
                    relevant = False
                    computed_voi = Decimal(0)
            if not relevant or deduplicated:
                self._governance.set_source_binding_state(
                    project.project_id,
                    acquisition.binding.binding_id,
                    state="DETACHED",
                    updated_at=self._clock.now().isoformat(),
                )
            low_voi_streak = (
                low_voi_streak + 1 if computed_voi <= limits.saturation_voi_threshold else 0
            )
            investigation = self._investigations.record_wave(
                investigation,
                observation_count=(1 if relevant and not deduplicated else 0),
                lead_count=(1 if relevant and not deduplicated else 0),
                claim_candidate_count=(1 if relevant and not deduplicated else 0),
                remaining_target_gaps=(
                    ()
                    if confirmations.maximum >= limits.min_independent_confirmations
                    else (f"counterevidence:{hypothesis.hypothesis_id}",)
                ),
            )
            checkpoint_digest = investigation.checkpoint_digest
            if plan.checkpoint_after:
                paused = self._investigations.pause(investigation)
                checkpoint_digest = paused.checkpoint_digest
                if checkpoint_digest is None:
                    raise ValueError("counter-search checkpoint digest is missing")
                investigation = self._investigations.resume(
                    paused,
                    expected_checkpoint_digest=checkpoint_digest,
                )
            confirmation_after = confirmations.maximum
            confirmation_before = confirmation_after - (
                1 if relevant and contributes_confirmation else 0
            )
            wave = CounterWaveRecord(
                wave_index=used_waves,
                route_id=plan.route_id,
                connector_id=plan.connector_id,
                priority=plan.priority,
                depth=plan.depth,
                excursion=plan.excursion,
                result_count=len(acquired_spans),
                document_count=1,
                byte_count=acquisition.receipt.byte_size,
                tool_calls=2,
                cost_microunits=plan.estimated_cost_microunits,
                deduplicated=deduplicated,
                computed_voi=computed_voi,
                sufficiency_before=f"confirmations:{confirmation_before}",
                sufficiency_after=f"confirmations:{confirmation_after}",
                decision_rank_before=rank_before,
                decision_rank_after=decision_rank,
                gate_terminal=gate.terminal,
                accepted_evidence_refs=(() if deduplicated else gate.accepted_evidence_refs),
                executed_track_ids=gate.executed_track_ids,
                checkpoint_digest=checkpoint_digest,
            )
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
        if loop_terminal is None:
            if budget_exhausted:
                loop_terminal = CounterLoopTerminal.BUDGET_EXHAUSTED
            elif counter_refs and support_refs:
                loop_terminal = CounterLoopTerminal.ABSTAINED
            elif policy_blocks and not waves:
                loop_terminal = CounterLoopTerminal.POLICY_BLOCKED
            elif authority_blocks and not waves:
                loop_terminal = CounterLoopTerminal.AUTHORITY_REQUIRED
            elif abstention_blocks:
                loop_terminal = CounterLoopTerminal.ABSTAINED
            else:
                loop_terminal = CounterLoopTerminal.SEARCH_SATURATED
        if (
            counter_refs
            and support_refs
            and loop_terminal
            not in {
                CounterLoopTerminal.ELIMINATED_WITHIN_SCOPE,
                CounterLoopTerminal.SUPPORTED,
            }
        ):
            loop_terminal = CounterLoopTerminal.ABSTAINED
        if not plans:
            raise ValueError("multi-wave counter-search produced no challenger plan")
        gate_terminal = {
            CounterLoopTerminal.ELIMINATED_WITHIN_SCOPE: (
                CounterReviewTerminal.ELIMINATED_WITHIN_SCOPE
            ),
            CounterLoopTerminal.SUPPORTED: CounterReviewTerminal.SUPPORTED,
            CounterLoopTerminal.SEARCH_SATURATED: CounterReviewTerminal.UNRESOLVED_NO_RESULTS,
            CounterLoopTerminal.BUDGET_EXHAUSTED: CounterReviewTerminal.UNRESOLVED_FAILED,
            CounterLoopTerminal.POLICY_BLOCKED: CounterReviewTerminal.UNRESOLVED_POLICY_BLOCKED,
            CounterLoopTerminal.AUTHORITY_REQUIRED: CounterReviewTerminal.UNRESOLVED_AUTHORITY,
            CounterLoopTerminal.ABSTAINED: CounterReviewTerminal.UNRESOLVED_CONFLICT,
        }[loop_terminal]
        accepted_refs = (
            tuple(dict.fromkeys(counter_refs))
            if loop_terminal == CounterLoopTerminal.ELIMINATED_WITHIN_SCOPE
            else tuple(dict.fromkeys(support_refs))
            if loop_terminal == CounterLoopTerminal.SUPPORTED
            else ()
        )
        gate = self._reviewer.aggregate(
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
        stopped = self._investigations.stop(
            investigation,
            reason=self._loop_stop_reason(loop_terminal),
        )
        elapsed = max(0, int((self._clock.now() - started_at).total_seconds()))
        budget = CounterSearchBudgetState(
            max_waves=limits.max_waves,
            max_results=limits.max_results,
            max_documents=limits.max_documents,
            max_bytes=limits.max_bytes,
            max_model_calls=limits.max_model_calls,
            max_tool_calls=limits.max_tool_calls,
            max_time_seconds=limits.max_time_seconds,
            max_cost_microunits=limits.max_cost_microunits,
            max_depth=limits.max_depth,
            used_waves=used_waves,
            used_results=used_results,
            used_documents=used_documents,
            used_bytes=used_bytes,
            used_model_calls=0,
            used_tool_calls=used_tool_calls,
            used_time_seconds=min(elapsed, limits.max_time_seconds),
            used_cost_microunits=used_cost,
        )
        return CriticalCounterSearchExecution(
            projection=self._finalizer.multi(
                project=project,
                thread=thread,
                portfolio=portfolio,
                hypothesis=hypothesis,
                investigation=stopped,
                plans=plans,
                gate=gate,
                loop_terminal=loop_terminal,
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
        draft: dict[str, object] = {
            "search_intent_id": self._ids.new("search-intent"),
            "project_id": plan.project_id,
            "thread_id": plan.thread_id,
            "investigation_id": plan.investigation_id,
            "evidence_group": f"counterevidence:{plan.hypothesis_id}",
            "query_families": plan.query_families,
            "connector_id": plan.connector_id,
            "selector": plan.selector,
            "max_waves": plan.max_waves,
            "max_results": plan.max_results,
            "stop_conditions": (
                "INDEPENDENT_GATE_TERMINAL",
                "SEARCH_SATURATED",
                "POLICY_BLOCKED",
                "BUDGET_EXHAUSTED",
            ),
            "policy_id": plan.policy_id,
            "policy_revision": plan.policy_revision,
            "policy_digest": plan.policy_digest,
            "state": "READY",
            "created_at": self._clock.now(),
        }
        return SearchIntent.model_validate(
            {
                **draft,
                "intent_digest": domain_digest(
                    "COUNTER_SEARCH_INTENT", "1.0.0", canonical_payload(draft)
                ),
            }
        )

    def _commit_evidence_candidate(
        self,
        *,
        project_id: str,
        thread: WorkThread,
        plan: CounterSearchPlan,
        intent: SearchIntent,
        source: EvidenceSourceRecord,
        spans: tuple[EvidenceSpan, ...],
        terminal: CounterReviewTerminal,
    ) -> None:
        if not spans:
            raise ValueError("gate accepted no evidence spans")
        span = spans[0]
        lead_draft: dict[str, object] = {
            "lead_id": self._ids.new("counterevidence-lead"),
            "project_id": project_id,
            "investigation_id": plan.investigation_id,
            "search_intent_id": intent.search_intent_id,
            "source_id": source.source_id,
            "span_id": span.span_id,
            "evidence_group": f"counterevidence:{plan.hypothesis_id}",
            "state": "CLAIM_CANDIDATE",
            "information_value": "CRITICAL_COUNTEREVIDENCE",
            "authority_basis": "independent deterministic gate pass",
            "created_at": self._clock.now(),
        }
        lead = AcquisitionLead.model_validate(
            {
                **lead_draft,
                "lead_digest": domain_digest(
                    "COUNTEREVIDENCE_LEAD", "1.0.0", canonical_payload(lead_draft)
                ),
            }
        )
        staged = self._evidence_graph.stage_link(
            project_id=project_id,
            target_type="HYPOTHESIS",
            target_id=plan.hypothesis_id,
            relation=(
                "CONTRADICTS"
                if terminal == CounterReviewTerminal.ELIMINATED_WITHIN_SCOPE
                else "SUPPORTS"
            ),
            span_ids=tuple(item.span_id for item in spans),
            observed_statement="\n".join(item.exact_text for item in spans),
            thread_id=thread.thread_id,
            conditions={
                "source_territory": plan.source_territory,
                "alternative_explanation": plan.alternative_explanation_track,
            },
            applicability="INDEPENDENT_CRITICAL_REVIEW_CANDIDATE",
            independence_group=plan.independence_group,
            lead=lead,
        )
        self._evidence_unit_of_work.commit(staged)

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

    @staticmethod
    def _loop_stop_reason(terminal: CounterLoopTerminal) -> str:
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
