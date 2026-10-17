"""Budget consumption for one bounded counter-search execution."""

from dataclasses import dataclass
from datetime import datetime

from thoth.application.services.connector_service import ConnectorAcquisition
from thoth.application.services.counter_search_calculations import budget_snapshot
from thoth.domain.counterevidence import CounterSearchBudgetState
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.policy import CounterSearchLimits


@dataclass
class CounterSearchUsage:
    started_at: datetime
    used_waves: int = 0
    used_results: int = 0
    used_documents: int = 0
    used_bytes: int = 0
    used_tool_calls: int = 0
    used_cost: int = 0

    def exhausted(self, limits: CounterSearchLimits, estimated_cost: int, now: datetime) -> bool:
        elapsed = max(0, int((now - self.started_at).total_seconds()))
        return (
            self.used_waves >= limits.max_waves
            or self.used_documents >= limits.max_documents
            or self.used_results >= limits.max_results
            or self.used_bytes >= limits.max_bytes
            or self.used_tool_calls + 2 > limits.max_tool_calls
            or self.used_cost + estimated_cost > limits.max_cost_microunits
            or elapsed >= limits.max_time_seconds
        )

    def remaining(self, limits: CounterSearchLimits) -> tuple[int, int, bool]:
        remaining_bytes = limits.max_bytes - self.used_bytes
        remaining_results = limits.max_results - self.used_results
        return remaining_bytes, remaining_results, remaining_bytes <= 0 or remaining_results <= 0

    def record_success(
        self, acquisition: ConnectorAcquisition, estimated_cost: int, remaining_results: int
    ) -> tuple[EvidenceSpan, ...]:
        self.used_waves += 1
        self.used_documents += 1
        self.used_tool_calls += 2
        self.used_cost += estimated_cost
        self.used_bytes += acquisition.receipt.byte_size
        acquired_spans = acquisition.ingestion.evidence_candidates[:remaining_results]
        self.used_results += len(acquired_spans)
        return acquired_spans

    def snapshot(self, limits: CounterSearchLimits, now: datetime) -> CounterSearchBudgetState:
        elapsed = max(0, int((now - self.started_at).total_seconds()))
        return budget_snapshot(
            limits=limits,
            used_waves=self.used_waves,
            used_results=self.used_results,
            used_documents=self.used_documents,
            used_bytes=self.used_bytes,
            used_tool_calls=self.used_tool_calls,
            used_cost=self.used_cost,
            elapsed=elapsed,
        )
