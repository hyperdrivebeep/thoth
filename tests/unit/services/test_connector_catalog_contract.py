"""Readiness, filtering and search projection observations before extraction."""

from typing import cast

import pytest
from pydantic import JsonValue

from thoth.application.services.connector_service import (
    ConnectorService,
    public_web_search_attempts,
)
from thoth.domain.connectors import ConnectorAccessRequest
from thoth.domain.policy import (
    AcquisitionRoute,
    AuthoritativeExecutionPolicy,
    CounterSearchRoute,
    PolicyExpectation,
)
from thoth.domain.public_web_access import PROJECT_PUBLIC_WEB_CONNECTOR_ID as WEB
from thoth.domain.workspace_setup import WorkspaceSetupState


class Grant(str):
    events: list[str]

    def __new__(cls, value: str, events: list[str]) -> "Grant":
        result = super().__new__(cls, value)
        result.events = events
        return result

    def __eq__(self, other: object) -> bool:
        self.events.append("readiness")
        return str(self) == other

    __hash__ = str.__hash__


class Item(dict[str, JsonValue]):
    def __init__(self, identifier: str, egress: str) -> None:
        super().__init__(
            connector_id=identifier,
            egress_class=egress,
            marker=identifier,
            index_freshness="old",
            configured_selectors=["old"],
        )
        self.reads: list[str] = []

    def __getitem__(self, key: str) -> JsonValue:
        self.reads.append(key)
        return super().__getitem__(key)


def policy() -> AuthoritativeExecutionPolicy:
    return AuthoritativeExecutionPolicy.model_validate(
        {
            "policy_id": "policy",
            "project_id": "p",
            "policy_revision": 1,
            "policy_digest": "a" * 64,
            "connector_allowlist": ("local", WEB, "egress-denied"),
            "connector_allowed_egress_classes": ("NONE", "ALLOWLISTED_EXTERNAL"),
            "max_source_security_class": "RESTRICTED",
            "sandbox_runtime_allowlist": (),
            "sandbox_network_policy": "DENY_ALL",
            "sandbox_allowed_hosts": (),
            "public_web_enabled": True,
            "preferred_hosts": ("arxiv.org",),
            "workspace_internet_grant_id": "grant",
            "acquisition_routes": (
                AcquisitionRoute(
                    evidence_group="g",
                    match_terms=("m",),
                    connector_id="local",
                    selector={"kind": "acquisition"},
                    query_families=("q",),
                ),
            ),
            "counter_search_routes": (
                CounterSearchRoute(
                    target_loci=("INPUT_MATERIAL_DATA",),
                    connector_id="local",
                    selector={"kind": "counter"},
                    query_families=("q",),
                    source_territory="source",
                    independence_group="group",
                    alternative_explanation="alternative",
                    counter_match_terms=("counter",),
                ),
            ),
        }
    )


class Catalog(ConnectorService):
    def __init__(self) -> None:
        self.events: list[str] = []
        self.policy = policy()
        self.setup = WorkspaceSetupState(
            internet_consent="ALLOWED", internet_grant_id="grant"
        ).model_copy(update={"internet_grant_id": Grant("grant", self.events)})
        self.items = (
            Item("excluded", "NONE"),
            Item(WEB, "ALLOWLISTED_EXTERNAL"),
            Item("local", "NONE"),
            Item("egress-denied", "DENIED"),
        )
        self.fault_at: str | None = None
        self.fault = RuntimeError("controlled catalog read failure")

        def setup() -> WorkspaceSetupState:
            self.event("setup")
            return self.setup

        self._workspace_setup = setup

    def event(self, name: str) -> None:
        self.events.append(name)
        if name == self.fault_at:
            raise self.fault

    def _authorize(
        self, project_id: str, *, expectation: PolicyExpectation | None
    ) -> AuthoritativeExecutionPolicy:
        assert project_id == "p" and expectation is None
        self.event("authorize")
        return self.policy

    def capabilities(self) -> tuple[dict[str, JsonValue], ...]:
        self.event("capabilities")
        return self.items


@pytest.mark.parametrize(
    "case",
    ["ready", "disabled", "no_hosts", "denied", "no_grant", "stale_grant", "not_allowed", "egress"],
)
def test_catalog_order_filtering_and_selector_identity(case: str) -> None:
    h = Catalog()
    changes: dict[str, dict[str, object]] = {
        "disabled": {"public_web_enabled": False},
        "no_hosts": {"preferred_hosts": ()},
        "not_allowed": {"connector_allowlist": ("local", "egress-denied")},
        "egress": {"connector_allowed_egress_classes": ("NONE",)},
    }
    h.policy = h.policy.model_copy(update=changes.get(case, {}))
    if case == "denied":
        h.setup = h.setup.model_copy(update={"internet_consent": "DENIED"})
    elif case == "no_grant":
        h.setup = h.setup.model_copy(update={"internet_grant_id": None})
    elif case == "stale_grant":
        h.setup = h.setup.model_copy(update={"internet_grant_id": Grant("stale", h.events)})
    original = tuple(dict(item) for item in h.items)
    result = h.research_catalog("p")
    assert h.events == [
        "authorize",
        "setup",
        *([] if case in {"denied", "no_grant"} else ["readiness"]),
        "capabilities",
    ]
    assert [item["connector_id"] for item in result] == (
        [WEB, "local"] if case == "ready" else ["local"]
    )
    assert tuple(dict(item) for item in h.items) == original
    assert h.items[0].reads == ["connector_id"]
    assert h.items[3].reads == ["connector_id", "egress_class"]
    if case == "not_allowed":
        assert h.items[1].reads == ["connector_id"]
    elif case == "egress":
        assert h.items[1].reads == ["connector_id", "egress_class"]
    elif case != "ready":
        assert h.items[1].reads == ["connector_id", "egress_class", "connector_id"]
    local = result[-1]
    assert list(local) == [
        "connector_id",
        "egress_class",
        "marker",
        "index_freshness",
        "configured_selectors",
        "unvisited_scope",
    ]
    assert local["index_freshness"] == "UNKNOWN" and local["unvisited_scope"] == "NOT_ENUMERATED"
    selectors = cast(list[JsonValue], local["configured_selectors"])
    assert len(selectors) == 2
    assert selectors[0] is h.policy.acquisition_routes[0].selector
    assert selectors[1] is h.policy.counter_search_routes[0].selector


@pytest.mark.parametrize("point", ["authorize", "setup", "capabilities"])
def test_catalog_read_failures_keep_order_and_identity(point: str) -> None:
    h = Catalog()
    h.fault_at = point
    with pytest.raises(RuntimeError) as caught:
        h.research_catalog("p")
    assert caught.value is h.fault
    order = ["authorize", "setup", "readiness", "capabilities"]
    assert h.events == order[: order.index(point) + 1]
    assert all(item.reads == [] for item in h.items)


@pytest.mark.parametrize(
    "case", ["mode", "disabled", "no_hosts", "missing", "non_string", "empty", "blank", "valid"]
)
def test_search_projection_keeps_original_and_exact_scoped_query(case: str) -> None:
    p = policy()
    selector: dict[str, JsonValue] = {
        "mode": "SEARCH",
        "query": "  bounded query  ",
        "extra": "kept",
    }
    if case == "mode":
        selector["mode"] = "READ"
    elif case == "disabled":
        p = p.model_copy(update={"public_web_enabled": False})
    elif case == "no_hosts":
        p = p.model_copy(update={"preferred_hosts": ()})
    elif case == "missing":
        del selector["query"]
    elif case == "non_string":
        selector["query"] = 7
    elif case in {"empty", "blank"}:
        selector["query"] = "" if case == "empty" else "   "
    request = ConnectorAccessRequest.model_validate(
        {
            "actor_id": "actor",
            "project_id": "p",
            "connector_id": WEB,
            "operation": "DISCOVER",
            "selector": selector,
            "policy_id": "policy",
            "policy_revision": 1,
            "policy_digest": "a" * 64,
        }
    )
    original = request.model_dump(mode="json")
    attempts = public_web_search_attempts(request, p)
    if case == "valid":
        assert len(attempts) == 2 and attempts[1] is request and attempts[0] is not request
        assert attempts[0].selector == {
            "mode": "SEARCH",
            "query": "(  bounded query  ) (site:arxiv.org)",
            "extra": "kept",
        }
        assert attempts[0].selector is not request.selector
    else:
        assert len(attempts) == 1 and attempts[0] is request
    assert request.model_dump(mode="json") == original
