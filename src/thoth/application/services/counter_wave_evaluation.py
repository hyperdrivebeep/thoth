"""Evaluate and checkpoint one acquired counter-search wave."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from thoth.application.services.connector_service import ConnectorAcquisition
from thoth.application.services.critical_counter_search_finalization import (
    IndependentConfirmationTracker,
)
from thoth.application.services.investigation_service import InvestigationService
from thoth.domain.acquisition import SearchIntent
from thoth.domain.counterevidence import (
    CounterReviewTerminal,
    CounterSearchPlan,
    CounterWaveRecord,
    IndependentGateReview,
)
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.evidence_graph import EvidenceSourceRecord
from thoth.domain.investigation import InvestigationRecord
from thoth.domain.policy import CounterSearchLimits
from thoth.domain.project import WorkThread
from thoth.ports.governance import GovernanceStorePort
from thoth.ports.runtime import ClockPort


class CounterEvidenceCommitter(Protocol):
    def __call__(
        self,
        *,
        project_id: str,
        thread: WorkThread,
        plan: CounterSearchPlan,
        intent: SearchIntent,
        source: EvidenceSourceRecord,
        spans: tuple[EvidenceSpan, ...],
        terminal: CounterReviewTerminal,
    ) -> None: ...


@dataclass(frozen=True)
class CounterWaveOutcome:
    investigation: InvestigationRecord
    decision_rank: int
    low_voi_streak: int
    wave: CounterWaveRecord


class CounterWaveEvaluator:
    def __init__(
        self,
        *,
        governance: GovernanceStorePort,
        investigations: InvestigationService,
        clock: ClockPort,
        commit_evidence_candidate: CounterEvidenceCommitter,
    ) -> None:
        self._governance = governance
        self._investigations = investigations
        self._clock = clock
        self._commit_evidence_candidate = commit_evidence_candidate

    def evaluate(
        self,
        *,
        project_id: str,
        thread: WorkThread,
        plan: CounterSearchPlan,
        intent: SearchIntent,
        acquisition: ConnectorAcquisition,
        acquired_spans: tuple[EvidenceSpan, ...],
        gate: IndependentGateReview,
        investigation: InvestigationRecord,
        hypothesis_id: str,
        limits: CounterSearchLimits,
        wave_index: int,
        seen_content_digests: set[str],
        confirmations: IndependentConfirmationTracker,
        counter_refs: list[str],
        support_refs: list[str],
        executed_track_ids: list[str],
        decision_rank: int,
        low_voi_streak: int,
    ) -> CounterWaveOutcome:
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
                    project_id=project_id,
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
                project_id,
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
                else (f"counterevidence:{hypothesis_id}",)
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
            wave_index=wave_index,
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
        return CounterWaveOutcome(investigation, decision_rank, low_voi_streak, wave)
