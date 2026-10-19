"""Bookkeeping accepts one exact new owner, never unrelated or existing-row changes."""

import json
from collections.abc import Mapping
from copy import deepcopy

import pytest
from tests.atomicity.harness import (
    AllowedRow,
    Snapshot,
    assert_phase_delta,
    unchanged_or_operation_owner,
)
from tests.integration.storage_coverage_helpers import request


def encoded(value: Mapping[str, object]) -> str:
    return json.dumps(value, sort_keys=True)


def states() -> tuple[Snapshot, Snapshot, tuple[AllowedRow, ...]]:
    operation: dict[str, object] = {
        "operation_id": "operation:new",
        "project_id": "project:test",
        "method": "project/metadata/update",
        "idempotency_key": "new",
        "state": "FAILED",
    }
    owner: dict[str, object] = {
        "operation_id": "operation:new",
        "worker_id": "worker:synthetic",
        "process_id": 123,
        "process_marker": "marker",
        "host_token": "b" * 64,
    }
    before: Snapshot = {
        "operations": (),
        "operation_owners": (),
        "domain": (encoded({"value": 1}),),
    }
    after: Snapshot = {
        **before,
        "operations": (encoded(operation),),
        "operation_owners": (encoded(owner),),
    }
    return before, after, (AllowedRow("operations", operation, "added", {}),)


def test_exact_new_owner_and_ownerless_environment_are_allowed() -> None:
    before, after, allowed = states()
    assert_phase_delta(before, after, allowed)
    after["operation_owners"] = ()
    assert_phase_delta(before, after, allowed)


@pytest.mark.parametrize(
    "case", ["foreign", "two", "duplicate", "modify", "delete", "backfill", "domain"]
)
def test_owner_rules_never_weaken_other_row_or_domain_checks(case: str) -> None:
    before, after, allowed = states()
    own = json.loads(after["operation_owners"][0])
    old = {**own, "operation_id": "operation:old"}
    if case == "foreign":
        after["operation_owners"] = (encoded(old),)
    if case == "two":
        after["operation_owners"] += (encoded(old),)
    if case == "duplicate":
        after["operation_owners"] *= 2
    if case in {"modify", "delete"}:
        before["operation_owners"] = (encoded(old),)
        if case == "modify":
            after["operation_owners"] += (encoded({**old, "process_id": 456}),)
    if case == "backfill":
        before["operations"] = after["operations"]
    if case == "domain":
        after["domain"] = (encoded({"value": 2}),)
    with pytest.raises(AssertionError):
        assert_phase_delta(before, after, allowed)


def test_domain_projection_still_compares_every_domain_byte() -> None:
    before, after, _ = states()
    command = request("project/metadata/update", "new", {"project_id": "project:test"})
    assert unchanged_or_operation_owner(before, after, "operation_owners", command)
    assert unchanged_or_operation_owner(before, after, "domain", command)
    changed = deepcopy(after)
    changed["domain"] = (encoded({"value": 2}),)
    assert not unchanged_or_operation_owner(before, changed, "domain", command)
    changed["operation_owners"] += (
        encoded(
            {**json.loads(changed["operation_owners"][0]), "operation_id": "operation:foreign"}
        ),
    )
    with pytest.raises(AssertionError):
        unchanged_or_operation_owner(before, changed, "operation_owners", command)


@pytest.mark.parametrize(
    "case", ["valid", "foreign", "two", "modify", "delete", "backfill", "domain"]
)
def test_scope_view_keeps_raw_evidence_and_rejects_other_changes(case: str) -> None:
    from tests.integration.test_resource_scope_transition_boundaries import (
        Snapshot as ScopeSnapshot,
    )
    from tests.integration.test_resource_scope_transition_boundaries import (
        owner_adjusted,
    )

    before, after, _ = states()
    owner = json.loads(after["operation_owners"][0])
    old = {**owner, "operation_id": "operation:old"}
    if case == "foreign":
        after["operation_owners"] = (encoded(old),)
    if case == "two":
        after["operation_owners"] += (encoded(old),)
    if case in {"modify", "delete"}:
        before["operation_owners"] = (encoded(old),)
        if case == "modify":
            after["operation_owners"] += (encoded({**old, "process_id": 456}),)
    if case == "backfill":
        before["operations"] = after["operations"]
    if case == "domain":
        after["domain"] = (encoded({"value": 2}),)
    left = ScopeSnapshot({k: v for k, v in before.items() if k != "operations"})
    left.operation_rows = before["operations"]
    right = ScopeSnapshot({k: v for k, v in after.items() if k != "operations"})
    right.operation_rows = after["operations"]
    saved = deepcopy(right)
    if case == "valid":
        assert (
            owner_adjusted(left, right, "project:test", "project/metadata/update", ("new",)) == left
        )
    else:
        with pytest.raises(AssertionError):
            assert (
                owner_adjusted(left, right, "project:test", "project/metadata/update", ("new",))
                == left
            )
    assert right == saved
