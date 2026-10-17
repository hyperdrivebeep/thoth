"""Original Revision traversal and response contracts, including ordered storage reads."""

from datetime import UTC, datetime
from typing import cast

import pytest
from pydantic import JsonValue, ValidationError
from tests.unit.application.test_action_read_projection import NoCalls

from thoth.application.commands.revisions_full import RevisionFullHandlers
from thoth.application.services.baseline_service import BaselineService
from thoth.application.services.control_record_service import ControlRecordService
from thoth.application.services.revision_service import RevisionCommitService
from thoth.application.services.semantic_merge import SemanticThreeWayMergeService
from thoth.domain.actor import ActorRef
from thoth.domain.control_record import ControlRecord
from thoth.domain.enums import ActorKind, EntityType
from thoth.domain.revision import EntitySnapshot, SemanticRevision
from thoth.ports.control_record import ControlRecordStorePort
from thoth.ports.dependency import DependencyGraphPort
from thoth.ports.governance import GovernanceStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.runtime import ClockPort, IdGeneratorPort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode

NOW = datetime(2026, 10, 12, tzinfo=UTC)


def revision(
    letter: str, parents: tuple[str, ...] = (), entity: str = "object:1"
) -> SemanticRevision:
    return SemanticRevision(
        revision_id=f"revision:{letter}",
        project_id="p",
        entity_type=EntityType.DECISION_OBJECT,
        entity_id=entity,
        snapshot_id=f"snapshot:{letter}",
        parent_revision_digests=tuple(p * 64 for p in parents),
        actor=ActorRef(actor_id="actor:fixture", kind=ActorKind.AGENT, role="fixture"),
        reason="fixture",
        evidence_refs=(),
        affected_refs=(),
        revision_digest=letter * 64,
        created_at=NOW,
    )


def records() -> tuple[SemanticRevision, ...]:
    return (
        revision("a", ("c",)),
        revision("b", ("a",)),
        revision("c", ("b", "a")),
        revision("d", entity="other"),
    )


class Sources(NoCalls):
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.revisions = {r.revision_digest: r for r in records()}
        self.control = ControlRecord(
            control_revision_id="control:1",
            project_id="p",
            namespace="REVISION",
            record_type="BRANCH",
            record_id="branch:1",
            version=1,
            state="OPEN",
            payload={"original": "원문"},
            record_digest="e" * 64,
            created_at=NOW,
        )
        self.snapshots = {
            f"snapshot:{s}": EntitySnapshot(
                snapshot_id=f"snapshot:{s}",
                project_id="p",
                entity_type=EntityType.DECISION_OBJECT,
                entity_id="object:1",
                schema_version="1.0.0",
                content_digest="e" * 64,
                content=cast(dict[str, object], value),
            )
            for s, value in (
                ("a", {"field": 1, "gone": None, "keep": "원문"}),
                ("b", {"field": 2, "added": None, "keep": "원문"}),
            )
        }

    def read_heads(self, project: str) -> dict[str, str]:
        self.calls.append(("heads", project))
        return {"OBJECT:object:1": "c" * 64, "OBJECT:other": "d" * 64}

    def read_revision_by_digest(self, project: str, digest: str) -> SemanticRevision | None:
        self.calls.append(("revision", project, digest))
        return self.revisions.get(digest)

    def read_snapshot(self, identifier: str) -> EntitySnapshot | None:
        self.calls.append(("snapshot", identifier))
        return self.snapshots.get(identifier)

    def list(self, project: str, namespace: str, kind: str) -> tuple[ControlRecord, ...]:
        self.calls.append(("list", project, namespace, kind))
        return (self.control,)

    def read(self, project: str, namespace: str, identifier: str) -> ControlRecord | None:
        self.calls.append(("control", project, namespace, identifier))
        return self.control if identifier == "cs" else None

    def downstream(self, project: str, source: str) -> tuple[str, ...]:
        self.calls.append(("downstream", project, source))
        return ("last", "first", "last")


def handler(source: Sources) -> RevisionFullHandlers:
    return RevisionFullHandlers(
        records=cast(ControlRecordStorePort, source),
        controls=cast(ControlRecordService, NoCalls()),
        ledger=cast(LedgerPort, source),
        dependencies=cast(DependencyGraphPort, source),
        governance=cast(GovernanceStorePort, NoCalls()),
        commits=cast(RevisionCommitService, NoCalls()),
        clock=cast(ClockPort, NoCalls()),
        ids=cast(IdGeneratorPort, NoCalls()),
        semantic_merge=cast(SemanticThreeWayMergeService, NoCalls()),
        baselines=cast(BaselineService, NoCalls()),
    )


@pytest.mark.parametrize(
    "root,depth,letters",
    [
        ("c", 0, "c"),
        ("c", 1, "abc"),
        ("c", 2, "abc"),
        ("a", 1, "ac"),
        ("a", 2, "abc"),
        ("f", 20, "f"),
    ],
)
def test_ancestor_breadth_depth_missing_root_multiple_parents_and_cycle(
    root: str, depth: int, letters: str
) -> None:
    actual = RevisionFullHandlers._ancestor_digests(  # pyright: ignore[reportPrivateUsage]
        records(), root * 64, depth
    )
    assert actual == {x * 64 for x in letters}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "aggregate,root,depth,letters",
    [
        (None, None, 1, "abcd"),
        ("object:1", None, 1, "abc"),
        ("other", None, 1, "d"),
        ("", None, 1, ""),
        (None, "b", 1, "ab"),
        (None, "a", 1, "ac"),
        (None, "a", 2, "abc"),
        (None, "f", 2, ""),
    ],
)
async def test_graph_filter_nodes_edges_and_branch_read_order(
    aggregate: str | None, root: str | None, depth: int, letters: str
) -> None:
    source = Sources()
    value = await handler(source).graph_read(
        {
            "project_id": "p",
            "aggregate_id": aggregate,
            "revision_digest": root * 64 if root else None,
            "depth": depth,
        }
    )
    wanted = [r for r in records() if r.revision_id[-1] in letters]
    assert value == {
        "nodes": [r.model_dump(mode="json") for r in wanted],
        "edges": [
            {"from": p, "to": r.revision_digest, "relation": "PARENT"}
            for r in wanted
            for p in r.parent_revision_digests
        ],
        "branches": [source.control.model_dump(mode="json")],
    }
    assert list(value) == ["nodes", "edges", "branches"]
    assert source.calls == [
        ("heads", "p"),
        *(("revision", "p", x * 64) for x in ("d", "c", "a", "b")),
        ("list", "p", "REVISION", "BRANCH"),
    ]


@pytest.mark.asyncio
async def test_public_depth_zero_is_rejected_before_reading_even_though_helper_accepts_it() -> None:
    source = Sources()
    with pytest.raises(ValidationError):
        await handler(source).graph_read({"project_id": "p", "depth": 0})
    assert source.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [None, "cs", "missing", ""])
@pytest.mark.parametrize("kind", ["revision", "aggregate", "empty", "none"])
async def test_impact_optional_record_source_precedence_and_empty_source(
    change: str | None, kind: str
) -> None:
    source = Sources()
    params: dict[str, JsonValue] = {"project_id": "p", "change_set_id": change}
    if kind == "revision":
        params.update(revision_digest="a" * 64, aggregate_id="other")
    if kind == "aggregate":
        params["aggregate_id"] = "other"
    if kind == "empty":
        params["aggregate_id"] = ""
    value = await handler(source).impact_read(params)
    refs = ["last", "first", "last"] if kind in ("revision", "aggregate") else []
    expected = {
        "change_set": source.control.model_dump(mode="json") if change == "cs" else None,
        "explicit_dependencies": refs,
        "propagation_rules": "TYPED_DEPENDENCY_GRAPH",
        "affected_projections": refs,
        "cycles": [],
        "recomputation_plan": refs,
    }
    assert value == expected and list(value) == list(expected)
    calls: list[tuple[object, ...]] = []
    if change is not None:
        calls.append(("control", "p", "REVISION", change))
    if refs:
        calls.append(("downstream", "p", "a" * 64 if kind == "revision" else "other"))
    assert source.calls == calls


@pytest.mark.asyncio
async def test_legacy_diff_snapshot_read_order_and_presence_flags() -> None:
    source = Sources()
    value = await handler(source).diff_read(
        {"project_id": "p", "from_revision_digest": "a" * 64, "to_revision_digest": "b" * 64}
    )
    assert source.calls == [
        ("revision", "p", "a" * 64),
        ("snapshot", "snapshot:a"),
        ("revision", "p", "b" * 64),
        ("snapshot", "snapshot:b"),
    ]
    assert value == {
        "diff": [
            {
                "path": "/added",
                "before_present": False,
                "after_present": True,
                "before": None,
                "after": None,
            },
            {
                "path": "/field",
                "before_present": True,
                "after_present": True,
                "before": 1,
                "after": 2,
            },
            {
                "path": "/gone",
                "before_present": True,
                "after_present": False,
                "before": None,
                "after": None,
            },
        ],
        "algorithm": "THOTH_SEMANTIC_DIFF",
        "version": "1.0.0",
        "affected_paths": ["/added", "/field", "/gone"],
        "noncanonical_warning": True,
    }
    assert list(value) == ["diff", "algorithm", "version", "affected_paths", "noncanonical_warning"]


@pytest.mark.asyncio
async def test_missing_left_revision_stops_before_any_snapshot_or_right_read() -> None:
    source = Sources()
    with pytest.raises(RpcApplicationError) as caught:
        await handler(source).diff_read(
            {"project_id": "p", "from_revision_digest": "f" * 64, "to_revision_digest": "b" * 64}
        )
    assert (
        caught.value.code == RpcErrorCode.DOMAIN_REJECTED
        and caught.value.message == "revision content not found"
    )
    assert source.calls == [("revision", "p", "f" * 64)]


def test_legacy_ancestor_helper_alias_is_preserved() -> None:
    from thoth.application.commands.revision_read_projection import revision_ancestor_digests

    name = "_ancestor_digests"
    assert getattr(RevisionFullHandlers, name) is revision_ancestor_digests
