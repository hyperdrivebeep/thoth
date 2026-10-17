"""Freeze original publication-data construction and commit attribute evaluation order."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from thoth.application.services.hypothesis_service import HypothesisService
from thoth.domain.actor import ActorRef
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import ActorKind, EntityType
from thoth.domain.hypothesis_full import HypothesisPortfolioRecord, HypothesisRecord
from thoth.domain.revision import (
    EntitySnapshot,
    RevisionChangeSet,
    SemanticRevision,
    StagedRevision,
)

NOW = datetime(2026, 10, 14, tzinfo=UTC)
ORDER = [
    "heads",
    "record.dump",
    "id:snapshot",
    "ctor:snapshot",
    "ctor:actor",
    "ctor:revision",
    "commit.lookup",
    "id:changeset",
    "ctor:staged",
    "ctor:changeset",
    "commit.call",
]


class Trace:
    def __init__(self, failure: str | None = None) -> None:
        self.events: list[str] = []
        self.failure, self.error = failure, RuntimeError("publication dependency failure")
        self.models: dict[str, Any] = {}
        self.changed: RevisionChangeSet | None = None

    def hit(self, label: str) -> None:
        self.events.append(label)
        if label == self.failure:
            raise self.error


class Ledger:
    def __init__(self, trace: Trace, heads: dict[str, str]) -> None:
        self.trace, self.heads = trace, heads

    def read_heads(self, project: str) -> dict[str, str]:
        assert project == "p"
        self.trace.hit("heads")
        return dict(self.heads)


class IDs:
    def __init__(self, trace: Trace) -> None:
        self.trace, self.count = trace, 0

    def new(self, namespace: str) -> str:
        self.trace.hit("id:" + namespace)
        self.count += 1
        return namespace + ":" + str(self.count)


class Committer:
    def __init__(self, trace: Trace, branch: bool) -> None:
        self.trace = trace
        self.result = SimpleNamespace(committed_revision_ids=() if branch else ("revision:unit",))

    def __getattribute__(self, name: str) -> Any:
        if name == "commit":
            object.__getattribute__(self, "trace").hit("commit.lookup")
        return object.__getattribute__(self, name)

    def commit(self, change: RevisionChangeSet) -> Any:
        self.trace.changed = change
        self.trace.hit("commit.call")
        return self.result


class Record:
    """Observe dump without changing the real canonical record or its returned payload."""

    def __init__(self, trace: Trace, value: HypothesisRecord | HypothesisPortfolioRecord) -> None:
        self.trace, self.value = trace, value

    @property
    def project_id(self) -> str:
        return self.value.project_id

    @property
    def created_at(self) -> datetime:
        return self.value.created_at

    def model_dump(self, *, mode: str) -> dict[str, Any]:
        assert mode == "python"
        value = self.value.model_dump(mode=mode)
        self.trace.hit("record.dump")
        return value


def record(kind: str, parent: str | None) -> HypothesisRecord | HypothesisPortfolioRecord:
    common: dict[str, Any] = dict(
        project_id="p",
        object_id="o",
        portfolio_id="pf",
        revision_digest="b" * 64,
        supersedes_revision_digest=parent,
        created_at=NOW,
    )
    if kind == "hypothesis":
        return HypothesisRecord(
            **common,
            hypothesis_revision_id="revision:unit",
            hypothesis_id="h",
            statement="원래 문장",
            observed_problem="problem",
            primary_intent="EXPLORATORY",
            evidence_basis="source",
            scope={"scope": "unit"},
            evidence_refs=("e2", "e1"),
        )
    return HypothesisPortfolioRecord(
        **common,
        portfolio_revision_id="revision:unit",
        hypothesis_refs=("h2", "h1"),
        unknown_reserve={"next_checks": ["inspect"]},
    )


def harness(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    parent: str | None,
    *,
    failure: str | None = None,
    mismatch: bool = False,
    branch: bool = False,
) -> tuple[Any, Trace, Record, Committer, str]:
    trace = Trace(failure)
    value = record(kind, parent)
    identifier = "h" if kind == "hypothesis" else "pf"
    heads = {} if parent is None else {"HYPOTHESIS:" + identifier: parent}
    if mismatch:
        heads["HYPOTHESIS:" + identifier] = "c" * 64
    committer = Committer(trace, branch)
    service = object.__new__(HypothesisService)
    vars(service).update(_ledger=Ledger(trace, heads), _ids=IDs(trace), _commits=committer)
    for cls, name in (
        (EntitySnapshot, "snapshot"),
        (ActorRef, "actor"),
        (SemanticRevision, "revision"),
        (StagedRevision, "staged"),
        (RevisionChangeSet, "changeset"),
    ):
        original = cls.__init__

        def initialize(
            instance: Any, *args: Any, label: str = name, constructor: Any = original, **kwargs: Any
        ) -> None:
            constructor(instance, *args, **kwargs)
            trace.models[label] = instance
            trace.hit("ctor:" + label)

        monkeypatch.setattr(cls, "__init__", initialize)
    return service, trace, Record(trace, value), committer, identifier


def invoke(
    service: Any, value: Record, identifier: str, parent: str | None, evidence: tuple[str, ...] = ()
) -> Any:
    return service._commit_record(
        record=value,
        entity_id=identifier,
        revision_id="revision:unit",
        revision_digest="b" * 64,
        supersedes=parent,
        event_type="hypothesis/unitPublication",
        evidence_refs=evidence,
    )


@pytest.mark.parametrize("kind", ["hypothesis", "portfolio"])
@pytest.mark.parametrize("parent", [None, "a" * 64])
@pytest.mark.parametrize("evidence", [(), ("e2", "e1")])
def test_record_data_ids_order_commit_lookup_and_actor_identity(
    monkeypatch: pytest.MonkeyPatch, kind: str, parent: str | None, evidence: tuple[str, ...]
) -> None:
    service, trace, wrapped, committer, identifier = harness(monkeypatch, kind, parent)
    before = wrapped.value.model_dump(mode="python")
    result = invoke(service, wrapped, identifier, parent, evidence)
    assert trace.events == ORDER
    assert result is committer.result and wrapped.value.model_dump(mode="python") == before
    change = trace.changed
    assert change is trace.models["changeset"] and change is not None
    snapshot, actor, revision, staged = (
        trace.models[name] for name in ("snapshot", "actor", "revision", "staged")
    )
    assert (
        change.staged_revisions == (staged,)
        and staged.snapshot is snapshot
        and staged.revision is revision
    )
    assert revision.actor is actor and change.actor is actor
    assert actor.model_dump(mode="python") == {
        "actor_id": "agent:hypothesis-service",
        "kind": ActorKind.AGENT,
        "role": "hypothesis-editor",
        "model_id": None,
        "tool_version": None,
        "project_id": None,
        "session_id": None,
        "role_assignment_ref": None,
    }
    assert snapshot.model_dump(mode="python") == {
        "snapshot_id": "snapshot:1",
        "project_id": "p",
        "entity_type": EntityType.HYPOTHESIS,
        "entity_id": identifier,
        "schema_version": "1.0.0",
        "content": before,
        "content_digest": domain_digest("ENTITY_SNAPSHOT", "1.0.0", canonical_payload(before)),
    }
    assert revision.model_dump(mode="python") == {
        "revision_id": "revision:unit",
        "project_id": "p",
        "entity_type": EntityType.HYPOTHESIS,
        "entity_id": identifier,
        "snapshot_id": "snapshot:1",
        "parent_revision_digests": () if parent is None else (parent,),
        "actor": actor.model_dump(mode="python"),
        "reason": "hypothesis/unitPublication",
        "evidence_refs": evidence,
        "affected_refs": (),
        "revision_digest": "b" * 64,
        "created_at": NOW,
        "schema_version": "1.0.0",
    }
    assert change.changeset_id == "changeset:2" and change.project_id == "p"
    assert change.expected_heads == ({} if parent is None else {"HYPOTHESIS:" + identifier: parent})
    assert change.reason == "hypothesis/unitPublication" and change.impact_plan.model_dump() == {
        "stale_refs": (),
        "invalidated_refs": (),
        "recalculate_refs": (),
    }


@pytest.mark.parametrize("kind", ["hypothesis", "portfolio"])
@pytest.mark.parametrize("parent", [None, "a" * 64])
def test_head_guard_prevents_all_dump_ids_and_commit_lookup(
    monkeypatch: pytest.MonkeyPatch, kind: str, parent: str | None
) -> None:
    service, trace, wrapped, _, identifier = harness(monkeypatch, kind, parent, mismatch=True)
    with pytest.raises(ValueError, match=r"^HYPOTHESIS_REVISION_CONFLICT$"):
        invoke(service, wrapped, identifier, parent)
    assert trace.events == ["heads"] and trace.models == {} and trace.changed is None


@pytest.mark.parametrize("kind", ["hypothesis", "portfolio"])
@pytest.mark.parametrize("failure", ORDER)
def test_original_exception_identity_and_no_later_evaluation(
    monkeypatch: pytest.MonkeyPatch, kind: str, failure: str
) -> None:
    service, trace, wrapped, _, identifier = harness(monkeypatch, kind, None, failure=failure)
    with pytest.raises(RuntimeError) as caught:
        invoke(service, wrapped, identifier, None)
    assert caught.value is trace.error
    assert trace.events == ORDER[: ORDER.index(failure) + 1]


@pytest.mark.parametrize("kind", ["hypothesis", "portfolio"])
def test_branched_result_retains_original_error_after_commit(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    service, trace, wrapped, _, identifier = harness(monkeypatch, kind, "a" * 64, branch=True)
    with pytest.raises(
        ValueError, match=r"^hypothesis commit branched because expected revision changed$"
    ):
        invoke(service, wrapped, identifier, "a" * 64)
    assert trace.events == ORDER
