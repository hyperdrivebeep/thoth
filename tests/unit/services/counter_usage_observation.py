"""Observe the complete controlled execution, without inspecting call frames."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta

from pytest import MonkeyPatch
from tests.unit.services.acquisition_coordinator_support import T0
from tests.unit.services.counter_wave_support import Harness

from thoth.application.services.critical_counter_search import CounterevidenceChallenger
from thoth.domain.connectors import ConnectorErrorCode, ConnectorFailure
from thoth.domain.counterevidence import CounterSearchPlan

CASES = (
    "normal",
    "initial_tools",
    "tools_equal",
    "tools_over",
    "results_zero",
    "bytes_zero",
    "forward_time",
    "backwards_time",
    "failure",
    "cancel",
    "cost_sources",
)


def plan_cost[**P](call: Callable[P, CounterSearchPlan]) -> Callable[P, CounterSearchPlan]:
    def changed(*args: P.args, **kwargs: P.kwargs) -> CounterSearchPlan:
        return call(*args, **kwargs).model_copy(update={"estimated_cost_microunits": 1})

    return changed


async def observe(case: str, monkeypatch: MonkeyPatch) -> dict[str, object]:
    h = Harness(("counter first", "counter second", "counter third"), confirmations=10)
    changes: dict[str, int] = {
        "initial_tools": 1,
        "tools_equal": 2,
        "tools_over": 3,
        "results_zero": 1,
        "bytes_zero": len("counter first"),
        "forward_time": 1,
        "cost_sources": 4,
    }
    field = {
        "initial_tools": "max_tool_calls",
        "tools_equal": "max_tool_calls",
        "tools_over": "max_tool_calls",
        "results_zero": "max_results",
        "bytes_zero": "max_bytes",
        "forward_time": "max_time_seconds",
        "cost_sources": "max_cost_microunits",
    }.get(case)
    if field is not None:
        h.policy = h.policy.model_copy(
            update={
                "counter_search_limits": h.policy.counter_search_limits.model_copy(
                    update={field: changes[case]}
                )
            }
        )
    if case == "normal":
        h.policy = h.policy.model_copy(
            update={
                "counter_search_limits": h.policy.counter_search_limits.model_copy(
                    update={"min_independent_confirmations": 2}
                )
            }
        )
    if case == "cost_sources":
        h.routes = tuple(r.model_copy(update={"estimated_cost_microunits": 3}) for r in h.routes)
        monkeypatch.setattr(
            CounterevidenceChallenger, "challenge", plan_cost(CounterevidenceChallenger.challenge)
        )
    if case in {"failure", "cancel"}:
        h.fail_acquire_at = 2
        h.failure = (
            asyncio.CancelledError("controlled cancellation")
            if case == "cancel"
            else ConnectorFailure(ConnectorErrorCode.DRIVER_ERROR, "controlled failure")
        )
    h.advance_seconds = {"forward_time": 2, "backwards_time": -10}.get(case, 0)
    calls = 0

    def now() -> datetime:
        nonlocal calls
        result = T0 + timedelta(seconds=h.clock.seconds, milliseconds=125 * calls)
        calls += 1
        h.events.append("clock:" + result.isoformat())
        return result

    monkeypatch.setattr(h.clock, "now", now)
    error: str | None = None
    projection: object = None
    try:
        projection = (await h.run()).projection
    except asyncio.CancelledError as exc:
        assert exc is h.failure
        error = "CancelledError:" + str(exc)
    except ValueError as exc:
        assert case == "initial_tools"
        error = "ValueError:" + str(exc)
    return {
        "events": h.events,
        "projection": projection,
        "error": error,
        "requests": len(h.requests),
        "commits": len(h.committed),
    }
