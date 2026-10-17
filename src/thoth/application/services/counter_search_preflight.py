"""Classify and count counter-search admission blocks."""

from dataclasses import dataclass
from enum import StrEnum

from thoth.application.services.critical_counter_search import IndependentGateReviewer
from thoth.domain.connectors import ConnectorFailure
from thoth.domain.counterevidence import CounterReviewTerminal, CounterSearchPlan
from thoth.domain.evidence_graph import EvidenceSourceRecord
from thoth.domain.policy import AuthoritativeExecutionPolicy, CounterSearchRoute


class CounterSearchBlock(StrEnum):
    POLICY = "POLICY"
    AUTHORITY = "AUTHORITY"
    ABSTENTION = "ABSTENTION"


@dataclass
class CounterSearchBlocks:
    policy: int = 0
    authority: int = 0
    abstention: int = 0

    def record(self, block: CounterSearchBlock) -> None:
        if block == CounterSearchBlock.POLICY:
            self.policy += 1
        elif block == CounterSearchBlock.AUTHORITY:
            self.authority += 1
        else:
            self.abstention += 1

    def record_failure(self, failure: ConnectorFailure) -> None:
        if failure.policy_denial is not None:
            self.policy += 1
        else:
            self.abstention += 1


def classify_counter_route(
    route: CounterSearchRoute,
    plan: CounterSearchPlan,
    policy: AuthoritativeExecutionPolicy,
    primary_sources: tuple[EvidenceSourceRecord, ...],
    reviewer: IndependentGateReviewer,
) -> CounterSearchBlock | None:
    if route.depth > policy.counter_search_limits.max_depth:
        return CounterSearchBlock.ABSTENTION
    gate = reviewer.preflight(plan=plan, primary_sources=primary_sources)
    if gate.terminal == CounterReviewTerminal.UNRESOLVED_AUTHORITY:
        return CounterSearchBlock.AUTHORITY
    if gate.terminal in {
        CounterReviewTerminal.UNRESOLVED_PROHIBITED_CONTEXT,
        CounterReviewTerminal.UNRESOLVED_TEMPORAL,
        CounterReviewTerminal.UNRESOLVED_INDEPENDENCE,
    }:
        return CounterSearchBlock.ABSTENTION
    if plan.connector_id not in policy.connector_allowlist:
        return CounterSearchBlock.POLICY
    return None
