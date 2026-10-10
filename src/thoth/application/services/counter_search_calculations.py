"""Pure terminal, gate input and reported-budget calculations after the stateful wave loop."""

from thoth.domain.counterevidence import (
    CounterLoopTerminal,
    CounterReviewTerminal,
    CounterSearchBudgetState,
    CounterWaveRecord,
)
from thoth.domain.policy import CounterSearchLimits


def resolve_loop_terminal(
    *,
    loop_terminal: CounterLoopTerminal | None,
    budget_exhausted: bool,
    counter_refs: list[str],
    support_refs: list[str],
    policy_blocks: int,
    authority_blocks: int,
    abstention_blocks: int,
    waves: list[CounterWaveRecord],
) -> CounterLoopTerminal:
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
    return loop_terminal


def aggregate_gate_inputs(
    loop_terminal: CounterLoopTerminal,
    counter_refs: list[str],
    support_refs: list[str],
) -> tuple[CounterReviewTerminal, tuple[str, ...]]:
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
    return gate_terminal, accepted_refs


def budget_snapshot(
    *,
    limits: CounterSearchLimits,
    used_waves: int,
    used_results: int,
    used_documents: int,
    used_bytes: int,
    used_tool_calls: int,
    used_cost: int,
    elapsed: int,
) -> CounterSearchBudgetState:
    return CounterSearchBudgetState(
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
