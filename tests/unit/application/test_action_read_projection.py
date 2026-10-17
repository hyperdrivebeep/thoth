"""Characterize the original Action handler responses before extracting their pure builders."""

from datetime import UTC, datetime
from typing import cast
from unittest.mock import Mock

import pytest
from pydantic import JsonValue

from thoth.application.commands.actions_full import ActionHandlers
from thoth.application.services.action_service import ActionService
from thoth.application.services.revision_service import CommitResult
from thoth.domain.action_full import ActionPlanRecord, ActionRecord
from thoth.domain.base import DomainModel
from thoth.domain.canonical import canonical_payload
from thoth.ports.action import ActionStorePort
from thoth.ports.decision_object import DecisionObjectStorePort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode

NOW = datetime(2026, 10, 12, tzinfo=UTC)


def action() -> ActionRecord:
    return ActionRecord(
        action_revision_id="revision:action",
        action_id="action:1",
        project_id="p",
        object_id="object:1",
        portfolio_id="portfolio:1",
        primary_purpose="DIAGNOSE",
        specification={"description": "한글 · 12 ms"},
        evidence_refs=(),
        expected_observation_or_change={},
        effect_vector={},
        impact_set={"unknown": None, "count": 0},
        risk_tier="R0",
        required_processes=("review", "trace"),
        required_roles=("observer",),
        revision_digest="a" * 64,
        created_at=NOW,
    )


def plan() -> ActionPlanRecord:
    return ActionPlanRecord(
        plan_revision_id="revision:plan",
        plan_id="plan:1",
        project_id="p",
        object_id="object:1",
        selected_action_refs=("action:1",),
        steps=(
            {
                "step_id": "step:1",
                "policy_state": "NOT_CLASSIFIED",
                "risk_tier": "R0",
                "impact": {"nested": {"value": "한글"}},
            },
            {"step_id": "step:2"},
        ),
        dependency_edges=({"from": "step:1", "to": "step:2"},),
        cumulative_impact={"count": 0},
        required_process_union=("review", "trace"),
        required_role_union=("observer",),
        auto_executable_frontier=("step:2", "step:1"),
        point_of_no_return_steps=("step:1",),
        revision_digest="b" * 64,
        created_at=NOW,
    )


class NoCalls:
    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"unexpected dependency access: {name}")


class ReadStore(NoCalls):
    def __init__(self, *, missing: bool = False) -> None:
        self.action = action()
        self.plan = plan()
        self.missing = missing
        self.calls: list[tuple[str, str, str, str | None]] = []

    def read_action(
        self, project: str, identifier: str, revision: str | None
    ) -> ActionRecord | None:
        self.calls.append(("action", project, identifier, revision))
        return None if self.missing else self.action

    def read_plan(
        self, project: str, identifier: str, revision: str | None
    ) -> ActionPlanRecord | None:
        self.calls.append(("plan", project, identifier, revision))
        return None if self.missing else self.plan


def handler(store: ReadStore) -> ActionHandlers:
    return ActionHandlers(
        store=cast(ActionStorePort, store),
        service=cast(ActionService, NoCalls()),
        objects=cast(DecisionObjectStorePort, NoCalls()),
    )


def test_summary_fields_order_and_original_record_are_preserved() -> None:
    item = action()
    before = canonical_payload(item)
    result = ActionHandlers._summary(item)  # pyright: ignore[reportPrivateUsage]
    assert list(result) == [
        "action_id",
        "object_id",
        "portfolio_id",
        "primary_purpose",
        "proposal_state",
        "decision_state",
        "policy_state",
        "authorization_state",
        "risk_tier",
        "freshness",
        "revision_digest",
    ]
    assert result == {
        "action_id": "action:1",
        "object_id": "object:1",
        "portfolio_id": "portfolio:1",
        "primary_purpose": "DIAGNOSE",
        "proposal_state": "CANDIDATE",
        "decision_state": "NOT_EVALUATED",
        "policy_state": "NOT_CLASSIFIED",
        "authorization_state": "NOT_REQUIRED",
        "risk_tier": "R0",
        "freshness": "CURRENT",
        "revision_digest": "a" * 64,
    }
    assert canonical_payload(item) == before


def test_step_returns_the_original_dictionary_and_preserves_missing_error() -> None:
    item = plan()
    assert ActionHandlers._step(item, "step:1") is item.steps[0]  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(RpcApplicationError) as caught:
        ActionHandlers._step(item, "absent")  # pyright: ignore[reportPrivateUsage]
    assert caught.value.code == RpcErrorCode.DOMAIN_REJECTED
    assert caught.value.message == "step not found"


class Before(DomainModel):
    z: int = 1
    removed: str = "old"
    keep: str = "same"
    nil: None = None


class After(DomainModel):
    z: int = 2
    added: str = "new"
    keep: str = "same"


def test_diff_keeps_sorted_added_removed_changed_and_null_semantics() -> None:
    assert ActionHandlers._diff(Before(), After()) == [  # pyright: ignore[reportPrivateUsage]
        {"path": "added", "before": None, "after": "new"},
        {"path": "removed", "before": "old", "after": None},
        {"path": "z", "before": 1, "after": 2},
    ]
    assert ActionHandlers._diff(Before(), Before()) == []  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("kind", ["action", "plan"])
def test_revision_result_preserves_flags_serialization_and_changed_fields(kind: str) -> None:
    commit = Mock(spec=CommitResult)
    commit.model_dump.return_value = {"disposition": "COMMITTED", "marker": "existing"}
    if kind == "action":
        before = action()
        after = before.model_copy(update={"revision_digest": "c" * 64})
        result = ActionHandlers._action_revision_result(before, after, cast(CommitResult, commit))  # pyright: ignore[reportPrivateUsage]
        expected_record = after.model_dump(mode="json")
        flag = "invalidated_decisions_plans_authorizations_executions"
    else:
        before_plan = plan()
        after_plan = before_plan.model_copy(update={"revision_digest": "c" * 64})
        result = ActionHandlers._plan_revision_result(  # pyright: ignore[reportPrivateUsage]
            before_plan, after_plan, cast(CommitResult, commit)
        )
        expected_record = after_plan.model_dump(mode="json")
        flag = "frontier_impact_auth_invalidations"
    assert list(result) == [kind, "semantic_diff", flag, "commit"]
    assert result[flag] is True
    assert result["semantic_diff"] == [
        {
            "path": "revision_digest",
            "before": ("a" if kind == "action" else "b") * 64,
            "after": "c" * 64,
        }
    ]
    assert result[kind] == expected_record
    assert result["commit"] == {"disposition": "COMMITTED", "marker": "existing"}
    commit.model_dump.assert_called_once_with(mode="json")


@pytest.mark.asyncio
async def test_plan_graph_preserves_complete_shape_order_and_nested_identity() -> None:
    store = ReadStore()
    before = canonical_payload(store.plan)
    result = await handler(store).plan_graph(
        {"project_id": "p", "plan_id": "plan:1", "revision_digest": "b" * 64}
    )
    assert result == {
        "nodes": list(store.plan.steps),
        "dependency_edges": list(store.plan.dependency_edges),
        "gates": [
            {"step_id": "step:1", "policy_state": "NOT_CLASSIFIED", "risk_tier": "R0"},
            {"step_id": "step:2", "policy_state": None, "risk_tier": None},
        ],
        "parallel_groups": [["step:2", "step:1"]],
        "point_of_no_return_steps": ["step:1"],
        "current_frontier": ["step:2", "step:1"],
    }
    assert list(result) == [
        "nodes",
        "dependency_edges",
        "gates",
        "parallel_groups",
        "point_of_no_return_steps",
        "current_frontier",
    ]
    assert cast(list[object], result["nodes"])[0] is store.plan.steps[0]
    assert store.calls == [("plan", "p", "plan:1", "b" * 64)]
    assert canonical_payload(store.plan) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["impact_read", "policy_read"])
@pytest.mark.parametrize("route", ["action", "plan", "step", "empty-step"])
async def test_impact_routes_keep_branch_priority_calls_and_record_identity(
    method: str, route: str
) -> None:
    store = ReadStore()
    params: dict[str, JsonValue] = {"project_id": "p", "revision_digest": "d" * 64}
    expected: dict[str, object]
    if route == "action":
        params.update(action_id="action:1", plan_id="plan:1", step_id="absent")
        expected = {
            "direct_impact": store.action.impact_set,
            "risk_tier": "R0",
            "required_processes": ["review", "trace"],
            "required_roles": ["observer"],
        }
    elif route == "plan":
        params.update(plan_id="plan:1")
        expected = {
            "cumulative_impact": store.plan.cumulative_impact,
            "required_process_union": ["review", "trace"],
            "required_role_union": ["observer"],
        }
    else:
        params.update(plan_id="plan:1", step_id="step:1" if route == "step" else "step:2")
        expected = {"step_impact": store.plan.steps[0]["impact"] if route == "step" else {}}
    before = (canonical_payload(store.action), canonical_payload(store.plan))
    if method == "policy_read":
        expected["policy_version"] = "project-policy:current"
    result = await getattr(handler(store), method)(params)
    assert result == expected
    assert list(result) == list(expected)
    assert store.calls == [
        (
            "action" if route == "action" else "plan",
            "p",
            "action:1" if route == "action" else "plan:1",
            "d" * 64,
        )
    ]
    assert (canonical_payload(store.action), canonical_payload(store.plan)) == before
    if route == "step":
        assert result["step_impact"] is store.plan.steps[0]["impact"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params,missing,message,calls",
    [
        (
            {"action_id": "missing", "plan_id": "plan:1"},
            True,
            "Action not found",
            [("action", "p", "missing", None)],
        ),
        ({"plan_id": "missing"}, True, "plan not found", [("plan", "p", "missing", None)]),
        (
            {"plan_id": "plan:1", "step_id": "absent"},
            False,
            "step not found",
            [("plan", "p", "plan:1", None)],
        ),
        ({}, False, "impact query needs action_id or plan_id", []),
    ],
)
async def test_impact_errors_do_not_fall_through_or_touch_another_dependency(
    params: dict[str, JsonValue],
    missing: bool,
    message: str,
    calls: list[tuple[str, str, str, str | None]],
) -> None:
    store = ReadStore(missing=missing)
    with pytest.raises(RpcApplicationError) as caught:
        await handler(store).impact_read({"project_id": "p", **params})
    assert caught.value.code == RpcErrorCode.DOMAIN_REJECTED
    assert caught.value.message == message
    assert store.calls == calls


def test_public_handler_surface_matches_original() -> None:
    import inspect

    assert tuple(
        name
        for name, _value in inspect.getmembers(ActionHandlers, inspect.iscoroutinefunction)
        if not name.startswith("_")
    ) == (
        "audit_read",
        "authorization_decide",
        "authorization_prepare",
        "authorization_read",
        "compensation_create",
        "create",
        "generate",
        "impact_read",
        "impact_recalculate",
        "list",
        "merge_propose",
        "plan_compose",
        "plan_graph",
        "plan_read",
        "plan_revalidate",
        "plan_revise",
        "policy_classify",
        "policy_read",
        "portfolio_compose",
        "portfolio_evaluate",
        "portfolio_list",
        "portfolio_read",
        "read",
        "recommend",
        "revise",
        "select",
        "step_add",
        "step_read",
        "step_revise",
    )


def test_extracted_functions_are_the_same_callable_as_legacy_static_helpers() -> None:
    from thoth.application.commands import action_read_projection

    for old, new in {
        "_summary": "action_summary",
        "_step": "plan_step",
        "_diff": "semantic_diff",
        "_action_revision_result": "action_revision_result",
        "_plan_revision_result": "plan_revision_result",
    }.items():
        assert getattr(ActionHandlers, old) is getattr(action_read_projection, new)
