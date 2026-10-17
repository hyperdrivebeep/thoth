"""Original list-query filtering, caller order and empty-string boundaries."""

from typing import cast

import pytest
from pydantic import JsonValue, ValidationError
from tests.unit.application.test_action_read_projection import NoCalls, action

from thoth.application.commands.actions_full import ActionHandlers
from thoth.application.services.action_service import ActionService
from thoth.domain.action_full import ActionPortfolioRecord, ActionRecord
from thoth.domain.canonical import canonical_payload
from thoth.ports.action import ActionStorePort
from thoth.ports.decision_object import DecisionObjectStorePort


class Store(NoCalls):
    def __init__(self) -> None:
        first = action()
        self.actions = (first.model_copy(update={"action_id": "last"}), first)
        item = ActionPortfolioRecord(
            portfolio_revision_id="revision:p",
            portfolio_id="portfolio:1",
            project_id="p",
            object_id="object:1",
            action_refs=(),
            decision_need="query fixture",
            mandatory_criteria=(),
            enhancing_criteria=(),
            revision_digest="a" * 64,
            created_at=first.created_at,
        )
        self.portfolios = (item.model_copy(update={"portfolio_id": "last"}), item)
        self.calls: list[tuple[str, str]] = []

    def list_actions(self, project: str) -> tuple[ActionRecord, ...]:
        self.calls.append(("actions", project))
        return self.actions

    def list_portfolios(self, project: str) -> tuple[ActionPortfolioRecord, ...]:
        self.calls.append(("portfolios", project))
        return self.portfolios


def handler(store: Store) -> ActionHandlers:
    return ActionHandlers(
        store=cast(ActionStorePort, store),
        service=cast(ActionService, NoCalls()),
        objects=cast(DecisionObjectStorePort, NoCalls()),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,matching",
    [
        ("object_id", "object:1"),
        ("portfolio_id", "portfolio:1"),
        ("purpose", "DIAGNOSE"),
        ("proposal_state", "CANDIDATE"),
        ("policy_state", "NOT_CLASSIFIED"),
        ("authorization_state", "NOT_REQUIRED"),
    ],
)
@pytest.mark.parametrize("choice", ["matching", "different", "empty", "none"])
async def test_action_filters_preserve_none_empty_and_iteration_order(
    field: str, matching: str, choice: str
) -> None:
    store = Store()
    selected = (
        matching
        if choice == "matching"
        else "different"
        if choice == "different"
        else ""
        if choice == "empty"
        else None
    )
    before = [canonical_payload(item) for item in store.actions]
    result = await handler(store).list({"project_id": "p", field: selected})
    rows = cast(list[dict[str, JsonValue]], result["actions"])
    assert [item["action_id"] for item in rows] == (
        ["last", "action:1"] if choice in {"matching", "none"} else []
    )
    assert list(result) == ["actions", "next_cursor"] and result["next_cursor"] is None
    assert store.calls == [("actions", "p")]
    assert [canonical_payload(item) for item in store.actions] == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,matching", [("object_id", "object:1"), ("decision_state", "NOT_EVALUATED")]
)
@pytest.mark.parametrize("choice", ["matching", "different", "empty", "none"])
async def test_portfolio_filters_preserve_none_empty_and_full_records(
    field: str, matching: str, choice: str
) -> None:
    store = Store()
    selected = (
        matching
        if choice == "matching"
        else "different"
        if choice == "different"
        else ""
        if choice == "empty"
        else None
    )
    result = await handler(store).portfolio_list({"project_id": "p", field: selected})
    assert result == {
        "portfolios": [item.model_dump(mode="json") for item in store.portfolios]
        if choice in {"matching", "none"}
        else []
    }
    assert store.calls == [("portfolios", "p")]


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["list", "portfolio_list", "impact_read"])
async def test_invalid_input_is_rejected_before_accessing_the_store(method: str) -> None:
    store = Store()
    with pytest.raises(ValidationError):
        await getattr(handler(store), method)({"project_id": "", "unknown": True})
    assert store.calls == []
