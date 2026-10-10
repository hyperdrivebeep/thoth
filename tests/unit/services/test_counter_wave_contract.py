"""Loop budgets and partial evidence are characterized without real clocks or network I/O."""

import asyncio
from dataclasses import replace
from typing import cast

import pytest
from pydantic import JsonValue
from tests.unit.services.counter_wave_support import Harness

from thoth.domain.connectors import ConnectorErrorCode, ConnectorFailure
from thoth.domain.enums import AuthorityState


@pytest.mark.parametrize(
    "limit", ["waves", "results", "documents", "bytes", "tools", "cost", "time"]
)
async def test_each_cumulative_budget_stops_before_another_io_and_keeps_partial_evidence(
    limit: str,
) -> None:
    h = Harness(("counter first", "counter second", "counter third"), confirmations=10)
    field, cap = {
        "waves": ("max_waves", 1),
        "results": ("max_results", 1),
        "documents": ("max_documents", 1),
        "bytes": ("max_bytes", len("counter first")),
        "tools": ("max_tool_calls", 2),
        "cost": ("max_cost_microunits", 1),
        "time": ("max_time_seconds", 1),
    }[limit]
    h.policy = h.policy.model_copy(
        update={
            "counter_search_limits": h.policy.counter_search_limits.model_copy(update={field: cap})
        }
    )
    h.advance_seconds = 2 if limit == "time" else 0
    result = await h.run()
    assert result.projection["loop_terminal"] == "BUDGET_EXHAUSTED"
    assert len(h.requests) == len(h.committed) == 1
    budget = cast(dict[str, JsonValue], result.projection["budget"])
    assert (
        budget["used_waves"],
        budget["used_results"],
        budget["used_documents"],
        budget["used_bytes"],
        budget["used_tool_calls"],
        budget["used_cost_microunits"],
        budget["used_model_calls"],
    ) == (
        1,
        1,
        1,
        len("counter first"),
        2,
        1,
        0,
    )
    assert budget["used_time_seconds"] == (1 if limit == "time" else 0)
    gate = cast(dict[str, JsonValue], result.projection["gate"])
    assert gate["terminal"] == "UNRESOLVED_FAILED" and gate["accepted_evidence_refs"] == []


async def test_duplicate_content_is_charged_and_detached_but_not_independently_confirmed() -> None:
    h = Harness(("counter duplicate", "counter duplicate", "counter distinct"))
    result = await h.run()
    assert result.projection["loop_terminal"] == "ELIMINATED_WITHIN_SCOPE"
    waves = cast(list[dict[str, JsonValue]], result.projection["waves"])
    assert [w["route_id"] for w in waves] == ["route:0", "route:1", "route:2"]
    assert [w["deduplicated"] for w in waves] == [False, True, False]
    assert [w["decision_rank_after"] for w in waves] == [-1, -1, -2]
    assert waves[1]["accepted_evidence_refs"] == []
    assert all(w["checkpoint_digest"] for w in waves)
    assert h.detached == ["binding:1"] and len(h.committed) == 2
    budget = cast(dict[str, JsonValue], result.projection["budget"])
    assert budget["used_waves"] == budget["used_documents"] == 3


async def test_shared_lineage_is_not_an_independent_confirmation_even_with_new_content() -> None:
    h = Harness(("counter first", "counter second", "counter third"))
    a = h.acquisitions[1]
    h.acquisitions[1] = replace(
        a, source=a.source.model_copy(update={"lineage_root_id": "lineage:0"})
    )
    result = await h.run()
    assert result.projection["loop_terminal"] == "ELIMINATED_WITHIN_SCOPE"
    waves = cast(list[dict[str, JsonValue]], result.projection["waves"])
    assert [w["deduplicated"] for w in waves] == [False, False, False]
    assert [w["sufficiency_after"] for w in waves] == [
        "confirmations:1",
        "confirmations:1",
        "confirmations:2",
    ]
    assert len(h.committed) == 3


async def test_two_low_voi_waves_stop_before_third_route() -> None:
    h = Harness(("unrelated first", "unrelated second", "counter third"))
    result = await h.run()
    assert result.projection["loop_terminal"] == "SEARCH_SATURATED"
    assert len(h.requests) == 2 and h.committed == []
    assert h.detached == ["binding:0", "binding:1"]


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("policy", "POLICY_BLOCKED"),
        ("authority", "AUTHORITY_REQUIRED"),
        ("prohibited", "ABSTAINED"),
        ("depth", "ABSTAINED"),
    ],
)
async def test_preflight_blocks_do_not_spend_io_budget(case: str, expected: str) -> None:
    h = Harness(("counter first", "counter second"))
    if case == "policy":
        h.policy = h.policy.model_copy(update={"connector_allowlist": ()})
    else:
        changes: dict[str, dict[str, object]] = {
            "authority": {"source_authority": AuthorityState.UNCLASSIFIED},
            "prohibited": {"context_tags": ("HIDDEN_HOLDOUT",)},
            "depth": {"depth": h.policy.counter_search_limits.max_depth + 1},
        }
        h.routes = tuple(r.model_copy(update=changes[case]) for r in h.routes)
    result = await h.run()
    assert result.projection["loop_terminal"] == expected
    assert h.requests == [] and h.committed == [] and result.projection["waves"] == []
    assert cast(dict[str, JsonValue], result.projection["budget"])["used_tool_calls"] == 0


async def test_conflicting_partial_evidence_overrides_budget_terminal() -> None:
    h = Harness(("counter first", "supports second", "counter third"))
    h.policy = h.policy.model_copy(
        update={
            "counter_search_limits": h.policy.counter_search_limits.model_copy(
                update={"max_cost_microunits": 2}
            )
        }
    )
    result = await h.run()
    assert result.projection["loop_terminal"] == "ABSTAINED"
    assert len(h.requests) == len(h.committed) == 2
    gate = cast(dict[str, JsonValue], result.projection["gate"])
    assert gate["terminal"] == "UNRESOLVED_CONFLICT" and gate["accepted_evidence_refs"] == []


async def test_head_change_keeps_spent_budget_and_prior_evidence() -> None:
    h = Harness(("counter first", "counter second", "counter third"))
    h.head_change_after = 2
    result = await h.run()
    assert result.projection["loop_terminal"] == "ABSTAINED"
    assert len(h.requests) == 2 and len(h.committed) == 1
    assert h.detached == ["binding:1"] and len(cast(list[object], result.projection["waves"])) == 1
    assert cast(dict[str, JsonValue], result.projection["budget"])["used_waves"] == 2


async def test_commit_failure_keeps_prior_evidence_and_spent_budget() -> None:
    h = Harness(("counter first", "counter second"))
    h.fail_commit_at = 2
    result = await h.run()
    assert result.projection["loop_terminal"] == "SEARCH_SATURATED"
    assert h.commit_attempts == 2 and len(h.committed) == 1 and h.detached == ["binding:1"]
    waves = cast(list[dict[str, JsonValue]], result.projection["waves"])
    assert waves[1]["computed_voi"] == "0" and waves[1]["sufficiency_after"] == "confirmations:1"
    assert cast(dict[str, JsonValue], result.projection["budget"])["used_tool_calls"] == 4


async def test_typed_connector_failure_is_not_counted_as_a_successful_wave() -> None:
    h = Harness(("counter first", "counter second"))
    h.fail_acquire_at = 2
    h.failure = ConnectorFailure(ConnectorErrorCode.DRIVER_ERROR, "controlled failure")
    result = await h.run()
    assert result.projection["loop_terminal"] == "ABSTAINED"
    assert len(h.requests) == 2 and len(h.committed) == 1
    assert cast(dict[str, JsonValue], result.projection["budget"])["used_waves"] == 1


async def test_cancel_propagates_with_prior_evidence_and_without_fake_finalization() -> None:
    h = Harness(("counter first", "counter second"))
    h.fail_acquire_at = 2
    h.failure = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await h.run()
    assert len(h.committed) == 1 and h.finalized is None and h.events[-1] == "io.raise"


async def test_no_challenger_plan_preserves_the_original_error() -> None:
    h = Harness(("counter first",))
    h.policy = h.policy.model_copy(
        update={
            "counter_search_limits": h.policy.counter_search_limits.model_copy(
                update={"max_tool_calls": 0}
            )
        }
    )
    with pytest.raises(ValueError, match="multi-wave counter-search produced no challenger plan"):
        await h.run()
    assert h.requests == [] and h.committed == [] and h.finalized is None
