"""Direct boundaries supplement the frozen pre-extraction execution traces."""

from datetime import timedelta

import pytest
from tests.unit.services.acquisition_coordinator_support import T0, acquisition, span

from thoth.application.services.counter_search_usage import CounterSearchUsage
from thoth.domain.policy import CounterSearchLimits


@pytest.mark.parametrize(("used_bytes", "used_results"), [(10, 0), (0, 2), (11, 3)])
def test_remaining_reports_zero_or_negative_without_clamping(
    used_bytes: int,
    used_results: int,
) -> None:
    usage = CounterSearchUsage(T0, used_bytes=used_bytes, used_results=used_results)
    assert usage.remaining(CounterSearchLimits(max_bytes=10, max_results=2)) == (
        10 - used_bytes,
        2 - used_results,
        True,
    )


@pytest.mark.parametrize(
    ("seconds", "elapsed", "exhausted"),
    [
        (-1.5, 0, False),
        (0.999, 0, False),
        (1, 1, True),
        (9.9, 1, True),
    ],
)
def test_time_truncation_lower_bound_and_snapshot_cap(
    seconds: float,
    elapsed: int,
    exhausted: bool,
) -> None:
    usage = CounterSearchUsage(T0)
    limits = CounterSearchLimits(max_time_seconds=1)
    now = T0 + timedelta(seconds=seconds)
    assert usage.exhausted(limits, 0, now) is exhausted
    assert usage.snapshot(limits, now).used_time_seconds == elapsed


@pytest.mark.parametrize(("cap", "exhausted"), [(3, True), (4, False)])
def test_next_two_tool_calls_fit_exactly(cap: int, exhausted: bool) -> None:
    usage = CounterSearchUsage(T0, used_tool_calls=2)
    assert usage.exhausted(CounterSearchLimits(max_tool_calls=cap), 0, T0) is exhausted


def test_success_keeps_full_receipt_charge_while_slicing_result_budget() -> None:
    first, second = span("one", "counter one"), span("two", "counter two")
    acquired = acquisition((first, second))
    usage = CounterSearchUsage(T0)
    selected = usage.record_success(acquired, 7, 1)
    assert selected == (first,) and selected[0] is first
    budget = usage.snapshot(CounterSearchLimits(), T0)
    assert (budget.used_waves, budget.used_documents, budget.used_tool_calls) == (1, 1, 2)
    assert budget.used_cost_microunits == 7
    assert budget.used_bytes == acquired.receipt.byte_size
    assert budget.used_results == 1 and budget.used_model_calls == 0
