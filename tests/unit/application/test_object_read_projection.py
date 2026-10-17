"""Freeze Object response builders and the read/downstream/state/relation evaluation order."""

from datetime import UTC, datetime
from typing import cast
from unittest.mock import Mock

import pytest
from pydantic import JsonValue
from tests.unit.application.test_action_read_projection import NoCalls

from thoth.application.commands.objects_full import DecisionObjectHandlers
from thoth.application.services.decision_object_service import DecisionObjectService
from thoth.application.services.revision_service import CommitResult
from thoth.domain.canonical import canonical_payload
from thoth.domain.decision_object_full import DecisionObjectRecord, ObjectRelationRecord
from thoth.domain.enums import ImpactStatus
from thoth.ports.decision_object import DecisionObjectStorePort
from thoth.ports.dependency import DependencyGraphPort
from thoth.ports.thread import ThreadStorePort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


def record() -> DecisionObjectRecord:
    return DecisionObjectRecord(
        object_revision_id="revision:1",
        object_id="object:1",
        project_id="p",
        thread_id="thread:1",
        purpose_statement="원문 12 ms",
        problem_frame="fixture",
        focus_refs=(),
        profile_refs=("profile:b", "profile:a"),
        materialization_trigger="fixture",
        trigger_evidence_refs=(),
        entry_criteria={},
        exit_criteria={},
        blockers=("B2", "B1"),
        relation_refs=("relation:1",),
        actor_or_agent_ref="actor:fixture",
        revision_digest="a" * 64,
        created_at=datetime(2026, 10, 12, tzinfo=UTC),
    )


class Store(NoCalls):
    def __init__(self, calls: list[tuple[object, ...]], missing: bool = False) -> None:
        self.calls, self.missing = calls, missing
        self.item = record()
        self.items = (self.item.model_copy(update={"object_id": "last"}), self.item)
        self.relations = tuple(
            ObjectRelationRecord(
                relation_revision_id=f"rr:{n}",
                relation_id=f"r:{n}",
                project_id="p",
                source_object_id="object:1",
                relation_type="DEPENDS_ON",
                target_ref=target,
                semantic_role="fixture",
                evidence_refs=(),
                actor_or_agent_ref="actor:fixture",
                valid_from=self.item.created_at,
                authority_state="UNCLASSIFIED",
                active=active,
                revision_digest="b" * 64,
                created_at=self.item.created_at,
            )
            for n, target, active in (
                (1, "target:A", True),
                (2, "target:B", False),
                (3, "target:A", True),
            )
        )

    def list_objects(self, project: str) -> tuple[DecisionObjectRecord, ...]:
        self.calls.append(("list_objects", project))
        return self.items

    def read_object(
        self, project: str, identifier: str, digest: str | None
    ) -> DecisionObjectRecord | None:
        self.calls.append(("read_object", project, identifier, digest))
        return None if self.missing else self.item

    def list_relations(self, project: str, identifier: str) -> tuple[ObjectRelationRecord, ...]:
        self.calls.append(("list_relations", project, identifier))
        return self.relations


class Dependencies(NoCalls):
    def __init__(self, calls: list[tuple[object, ...]]) -> None:
        self.calls = calls

    def downstream(self, project: str, source: str) -> tuple[str, ...]:
        self.calls.append(("downstream", project, source))
        return ("third", "first", "untracked")

    def read_states(self, project: str) -> dict[str, ImpactStatus]:
        self.calls.append(("read_states", project))
        return {
            "first": ImpactStatus.STALE,
            "ignored": ImpactStatus.INVALIDATED,
            "third": ImpactStatus.CURRENT,
        }


def handler(store: Store) -> DecisionObjectHandlers:
    return DecisionObjectHandlers(
        store=cast(DecisionObjectStorePort, store),
        service=cast(DecisionObjectService, NoCalls()),
        threads=cast(ThreadStorePort, NoCalls()),
        dependencies=cast(DependencyGraphPort, Dependencies(store.calls)),
    )


def test_summary_fields_values_and_array_order() -> None:
    item = record()
    before = canonical_payload(item)
    value = DecisionObjectHandlers._summary(item)  # pyright: ignore[reportPrivateUsage]
    expected = {
        "object_id": "object:1",
        "project_id": "p",
        "thread_id": "thread:1",
        "purpose_statement": "원문 12 ms",
        "lifecycle": "ACTIVE",
        "active_work_mode": "FRAMING",
        "profile_refs": ["profile:b", "profile:a"],
        "profile_state": "UNCLASSIFIED",
        "attention": "NONE",
        "blockers": ["B2", "B1"],
        "resolution": "OPEN",
        "verification": "NOT_ASSESSED",
        "disposition": "NONE",
        "freshness": "CURRENT",
        "working_head_digest": "a" * 64,
    }
    assert value == expected and list(value) == list(expected)
    assert canonical_payload(item) == before


def test_revision_result_keeps_diff_order_axes_and_commit_dump() -> None:
    before = record()
    after = before.model_copy(update={"attention": "REVIEW", "revision_digest": "b" * 64})
    commit = Mock(spec=CommitResult)
    commit.model_dump.return_value = {"disposition": "COMMITTED"}
    value = DecisionObjectHandlers._revision_result(before, after, cast(CommitResult, commit))  # pyright: ignore[reportPrivateUsage]
    assert list(value) == [
        "object",
        "semantic_diff",
        "affected_relations",
        "invalidations",
        "status_axes",
        "commit",
    ]
    assert value["object"] == after.model_dump(mode="json")
    assert value["semantic_diff"] == [
        {"path": "attention", "before": "NONE", "after": "REVIEW"},
        {"path": "revision_digest", "before": "a" * 64, "after": "b" * 64},
    ]
    assert value["affected_relations"] == ["relation:1"] and value["invalidations"] == []
    assert value["status_axes"] == {
        "lifecycle": "ACTIVE",
        "resolution": "OPEN",
        "verification": "NOT_ASSESSED",
        "disposition": "NONE",
        "freshness": "CURRENT",
        "attention": "REVIEW",
    }
    assert value["commit"] == {"disposition": "COMMITTED"}
    commit.model_dump.assert_called_once_with(mode="json")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,match",
    [
        ("thread_id", "thread:1"),
        ("lifecycle", "ACTIVE"),
        ("active_work_mode", "FRAMING"),
        ("profile_ref", "profile:a"),
        ("attention", "NONE"),
        ("blocker", "B1"),
    ],
)
@pytest.mark.parametrize("choice", ["match", "miss", "empty", "none"])
async def test_list_predicates_preserve_membership_none_empty_and_store_order(
    field: str, match: str, choice: str
) -> None:
    store = Store([])
    selected = (
        match
        if choice == "match"
        else "missing"
        if choice == "miss"
        else ""
        if choice == "empty"
        else None
    )
    value = await handler(store).list({"project_id": "p", field: selected})
    rows = cast(list[dict[str, JsonValue]], value["objects"])
    assert [row["object_id"] for row in rows] == (
        ["last", "object:1"] if choice in {"match", "none"} else []
    )
    assert list(value) == ["objects", "next_cursor"] and value["next_cursor"] is None
    assert store.calls == [("list_objects", "p")]


@pytest.mark.asyncio
async def test_impact_read_preserves_call_order_filters_duplicates_and_state_order() -> None:
    store = Store([])
    before = canonical_payload(store.item)
    value = await handler(store).impact_read(
        {"project_id": "p", "object_id": "object:1", "revision_digest": "a" * 64}
    )
    assert store.calls == [
        ("read_object", "p", "object:1", "a" * 64),
        ("downstream", "p", "OBJECT:object:1"),
        ("read_states", "p"),
        ("list_relations", "p", "object:1"),
    ]
    expected = {
        "source_ref": "OBJECT:object:1",
        "direct_relation_targets": ["target:A", "target:A"],
        "transitive_affected_refs": ["third", "first", "untracked"],
        "dependency_states": {"first": "STALE", "third": "CURRENT"},
        "recalculation_plan": ["third", "first"],
    }
    assert value == {"impact": expected}
    assert list(cast(dict[str, object], value["impact"])) == list(expected)
    assert canonical_payload(store.item) == before


@pytest.mark.asyncio
async def test_missing_object_stops_before_dependency_and_relation_reads() -> None:
    store = Store([], missing=True)
    with pytest.raises(RpcApplicationError) as caught:
        await handler(store).impact_read({"project_id": "p", "object_id": "absent"})
    assert (
        caught.value.code == RpcErrorCode.DOMAIN_REJECTED
        and caught.value.message == "object not found"
    )
    assert store.calls == [("read_object", "p", "absent", None)]


def test_legacy_static_helpers_alias_the_extracted_builders() -> None:
    from thoth.application.commands import object_read_projection

    for name, function in (
        ("_summary", object_read_projection.object_summary),
        ("_revision_result", object_read_projection.object_revision_result),
    ):
        assert getattr(DecisionObjectHandlers, name) is function
