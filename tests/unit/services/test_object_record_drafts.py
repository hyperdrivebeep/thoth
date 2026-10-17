"""Original Object service characterizations with recorded ports and real model codecs."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from thoth.application.services import decision_object_service as module
from thoth.application.services.decision_object_service import DecisionObjectService
from thoth.domain.decision_object_full import DecisionObjectRecord, ObjectCandidateRecord

NOW = datetime(2026, 10, 14, tzinfo=UTC)


class Trace:
    def __init__(self, fail: str | None = None) -> None:
        self.events: list[str] = []
        self.fail, self.error = fail, RuntimeError("recorded Object dependency failure")
        self.candidates: list[Any] = []
        self.objects: list[DecisionObjectRecord] = []
        self.drafts: list[dict[str, object]] = []
        self.candidate_data: dict[str, Any] = {}
        self.commits: list[Any] = []

    def hit(self, label: str) -> None:
        self.events.append(label)
        if label == self.fail:
            raise self.error


class IDs:
    def __init__(self, trace: Trace) -> None:
        self.trace, self.count = trace, 0

    def new(self, namespace: str) -> str:
        self.trace.hit("id:" + namespace)
        self.count += 1
        return namespace + ":" + str(self.count)


class Clock:
    def __init__(self, trace: Trace) -> None:
        self.trace, self.count = trace, 0

    def now(self) -> datetime:
        self.trace.hit("clock:" + str(self.count + 1))
        self.count += 1
        return NOW + timedelta(seconds=self.count)


class Ledger:
    def __init__(self, trace: Trace) -> None:
        self.trace = trace

    @contextmanager
    def transaction(self) -> Any:
        self.trace.events.append("transaction:enter")
        try:
            yield
        finally:
            self.trace.events.append("transaction:exit")


class Store:
    def __init__(self, trace: Trace, duplicate: bool, bad_profile: bool) -> None:
        self.trace, self.duplicate, self.bad_profile = trace, duplicate, bad_profile

    def read_profile(self, reference: str, version: int | None) -> Any:
        assert version is None
        self.trace.hit("profile:" + reference)
        return None if self.bad_profile else SimpleNamespace(profile_ref=reference, enabled=True)

    def list_objects(self, project: str) -> tuple[Any, ...]:
        assert project == "p"
        self.trace.hit("objects:list")
        return (
            (
                SimpleNamespace(
                    object_id="duplicate:1",
                    thread_id="t",
                    lifecycle="ACTIVE",
                    purpose_statement="PURPOSE",
                ),
            )
            if self.duplicate
            else ()
        )

    def add_candidate(self, value: Any) -> None:
        self.trace.candidates.append(value)
        self.trace.hit("store:candidate")

    def add_object(self, value: DecisionObjectRecord) -> None:
        self.trace.objects.append(value)
        self.trace.hit("store:object")

    def append_audit(self, value: Any) -> None:
        self.trace.hit("store:audit:" + value.event_type)


class Artifacts:
    def __init__(self, trace: Trace, bad: bool) -> None:
        self.trace, self.bad = trace, bad

    def read_evidence(self, reference: str) -> Any:
        self.trace.hit("evidence:" + reference)
        return SimpleNamespace(project_id="other" if self.bad else "p")


class Commits:
    def __init__(self, trace: Trace) -> None:
        self.trace = trace

    def commit(self, changeset: Any) -> Any:
        self.trace.hit("commit")
        self.trace.commits.append(changeset)
        return SimpleNamespace(
            committed_revision_ids=tuple(
                item.revision.revision_id for item in changeset.staged_revisions
            )
        )


class Candidate:
    def __init__(self, trace: Trace, record: ObjectCandidateRecord) -> None:
        self.trace, self.record = trace, record

    @property
    def candidate_id(self) -> str:
        self.trace.hit("candidate:id")
        return self.record.candidate_id

    @property
    def candidate_digest(self) -> str:
        return self.record.candidate_digest


def harness(
    monkeypatch: pytest.MonkeyPatch,
    *,
    duplicate: bool = False,
    bad_profile: bool = False,
    bad_evidence: bool = False,
    fail: str | None = None,
) -> tuple[Any, Trace]:
    trace = Trace(fail)
    candidate_validate, object_validate = (
        ObjectCandidateRecord.model_validate,
        DecisionObjectRecord.model_validate,
    )

    class CandidateCodec:
        @staticmethod
        def model_validate(data: dict[str, Any]) -> Candidate:
            record = candidate_validate(data)
            trace.candidate_data = data
            trace.hit("validate:candidate")
            return Candidate(trace, record)

    class ObjectCodec:
        @staticmethod
        def model_validate(data: dict[str, Any]) -> DecisionObjectRecord:
            record = object_validate(data)
            trace.hit("validate:object")
            return record

    monkeypatch.setattr(module, "ObjectCandidateRecord", CandidateCodec)
    monkeypatch.setattr(module, "DecisionObjectRecord", ObjectCodec)
    service = object.__new__(DecisionObjectService)
    vars(service).update(
        _store=Store(trace, duplicate, bad_profile),
        _artifacts=Artifacts(trace, bad_evidence),
        _ledger=Ledger(trace),
        _commits=Commits(trace),
        _ids=IDs(trace),
        _clock=Clock(trace),
    )
    original_digest = vars(DecisionObjectService)["_digest"].__func__

    def digest(data: dict[str, object]) -> str:
        trace.drafts.append(data)
        trace.hit("digest")
        return original_digest(data)

    monkeypatch.setattr(service, "_digest", digest)
    return service, trace


def inputs() -> dict[str, Any]:
    return dict(
        project_id="p",
        thread_id="t",
        purpose_statement="purpose",
        problem_frame="problem",
        focus_refs=("focus:2", "focus:1"),
        trigger_evidence_refs=("e",),
        profile_refs=("P1",),
        actor_ref="agent:unit",
        workstream_refs=("work:2", "work:1"),
        requirement_refs=("req:1",),
    )


def timeline(trace: Trace) -> list[str]:
    return [event for event in trace.events if not event.startswith("transaction:")]


def materialize_order(profiles: tuple[str, ...] = ("P1",), provided_id: bool = False) -> list[str]:
    return [
        "evidence:e",
        *["profile:" + p for p in profiles],
        "objects:list",
        "id:object-candidate",
        "clock:1",
        "validate:candidate",
        "store:candidate",
        "id:object-revision",
        *([] if provided_id else ["id:object"]),
        "candidate:id",
        "clock:2",
        "digest",
        "validate:object",
        "id:snapshot",
        "id:changeset",
        "commit",
        "store:object",
        "clock:3",
        "id:object-audit",
        "store:audit:object/materialized",
        "candidate:id",
        "clock:4",
        "id:object-audit",
        "store:audit:object/candidateCreated",
    ]


@pytest.mark.parametrize("object_id", [None, "", "object:provided"])
@pytest.mark.parametrize(
    "profiles,state", [((), "UNCLASSIFIED"), (("P1",), "MATCHED"), (("P1", "P2"), "AMBIGUOUS")]
)
def test_materialize_ports_order_optional_id_and_candidate_identity(
    monkeypatch: pytest.MonkeyPatch, object_id: str | None, profiles: tuple[str, ...], state: str
) -> None:
    service, trace = harness(monkeypatch)
    args = inputs()
    args.update(object_id=object_id, profile_refs=profiles)
    candidate, record, commit = service.materialize(**args)
    assert timeline(trace) == materialize_order(profiles, bool(object_id))
    assert trace.events.count("transaction:enter") == trace.events.count("transaction:exit") == 2
    assert candidate is trace.candidates[0]
    assert record is trace.objects[0] and record is not None
    assert record.object_id == (object_id or "object:3")
    assert record.entry_criteria == {
        "status": "PASS",
        "candidate_ref": candidate.record.candidate_id,
    }
    assert record.profile_refs == profiles and record.profile_state == state
    assert (
        record.focus_refs == args["focus_refs"]
        and record.workstream_refs == args["workstream_refs"]
    )
    assert candidate.record.created_at == NOW + timedelta(seconds=1)
    assert record.created_at == NOW + timedelta(seconds=2)
    assert commit.committed_revision_ids == (record.object_revision_id,)
    assert list(trace.candidate_data) == [
        "candidate_id",
        "project_id",
        "thread_id",
        "purpose_statement",
        "problem_frame",
        "focus_refs",
        "trigger_type",
        "trigger_evidence_refs",
        "profile_candidates",
        "profile_state",
        "duplicate_object_refs",
        "entry_validation",
        "permitted_transition",
        "candidate_state",
        "created_at",
        "candidate_digest",
    ]
    assert list(trace.drafts[0]) == [
        "object_revision_id",
        "object_id",
        "project_id",
        "thread_id",
        "parent_object_id",
        "purpose_statement",
        "problem_frame",
        "focus_refs",
        "workstream_refs",
        "requirement_refs",
        "profile_refs",
        "profile_state",
        "facets",
        "materialization_trigger",
        "trigger_evidence_refs",
        "entry_criteria",
        "exit_criteria",
        "active_work_mode",
        "lifecycle",
        "resolution",
        "verification",
        "disposition",
        "freshness",
        "attention",
        "blockers",
        "relation_refs",
        "actor_or_agent_ref",
        "protected_boundary_context",
        "created_at",
    ]


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("duplicate", "HOLD_DUPLICATE"),
        ("no_focus", "HOLD_ENTRY_INVALID"),
        ("followup_no_evidence", "HOLD_ENTRY_INVALID"),
    ],
)
def test_materialize_hold_stops_after_candidate(
    monkeypatch: pytest.MonkeyPatch, kind: str, expected: str
) -> None:
    service, trace = harness(monkeypatch, duplicate=kind == "duplicate")
    args = inputs()
    if kind == "no_focus":
        args["focus_refs"] = ()
    if kind == "followup_no_evidence":
        args.update(trigger_type="FOLLOWUP", trigger_evidence_refs=())
    candidate, record, commit = service.materialize(**args)
    assert candidate is trace.candidates[0] and candidate.record.permitted_transition == expected
    assert record is commit is None and trace.objects == trace.commits == trace.drafts == []
    expected_order = materialize_order()[:7]
    if kind == "followup_no_evidence":
        expected_order = expected_order[1:]
    assert timeline(trace) == expected_order


def test_materialize_explicit_empty_evidence_and_followup_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, trace = harness(monkeypatch)
    args = inputs()
    args.update(
        trigger_evidence_refs=(), parent_object_id="parent", trigger_type="EXPLICIT_USER_REQUEST"
    )
    _, record, _ = service.materialize(**args)
    assert record.parent_object_id == "parent" and record.trigger_evidence_refs == ()
    assert timeline(trace) == materialize_order()[1:]


@pytest.mark.parametrize("fault", ["evidence", "profile"])
def test_materialize_guards_stay_before_drafts(monkeypatch: pytest.MonkeyPatch, fault: str) -> None:
    service, trace = harness(
        monkeypatch, bad_evidence=fault == "evidence", bad_profile=fault == "profile"
    )
    with pytest.raises(ValueError):
        service.materialize(**inputs())
    assert timeline(trace) == (
        ["evidence:e"] if fault == "evidence" else ["evidence:e", "profile:P1"]
    )
    assert trace.candidates == trace.objects == []


@pytest.mark.parametrize(
    "failure",
    [
        "id:object-candidate",
        "clock:1",
        "validate:candidate",
        "store:candidate",
        "id:object-revision",
        "id:object",
        "clock:2",
        "digest",
        "validate:object",
    ],
)
def test_materialize_same_exception_and_no_later_calls(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    service, trace = harness(monkeypatch, fail=failure)
    with pytest.raises(RuntimeError) as caught:
        service.materialize(**inputs())
    assert caught.value is trace.error
    order = materialize_order()
    assert timeline(trace) == order[: order.index(failure) + 1]


def existing() -> DecisionObjectRecord:
    return DecisionObjectRecord(
        object_revision_id="revision:old",
        object_id="object:old",
        project_id="p",
        thread_id="t",
        purpose_statement="original",
        problem_frame="problem",
        focus_refs=("focus",),
        materialization_trigger="EXPLICIT_USER_REQUEST",
        trigger_evidence_refs=("e",),
        entry_criteria={},
        exit_criteria={},
        actor_or_agent_ref="agent:unit",
        revision_digest="a" * 64,
        receipt_ref="receipt:old",
        created_at=NOW,
    )


def test_revise_copy_updates_ids_time_and_removed_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    service, trace = harness(monkeypatch)
    current = existing()
    before = current.model_dump(mode="python")
    updates: dict[str, object] = {"purpose_statement": "revised", "workstream_refs": ("w2", "w1")}
    revised, _ = service.revise(
        current, updates=updates, event_type="object/updated", evidence_refs=("e",)
    )
    assert current.model_dump(mode="python") == before
    assert revised.purpose_statement == "revised" and revised.workstream_refs == ("w2", "w1")
    assert revised.supersedes_revision_digest == current.revision_digest
    assert revised.created_at == NOW + timedelta(seconds=1) and revised.receipt_ref is None
    assert "revision_digest" not in trace.drafts[0] and "receipt_ref" not in trace.drafts[0]
    assert updates == {"purpose_statement": "revised", "workstream_refs": ("w2", "w1")}
    assert timeline(trace) == [
        "evidence:e",
        "id:object-revision",
        "clock:1",
        "digest",
        "validate:object",
        "id:snapshot",
        "id:changeset",
        "commit",
        "store:object",
        "clock:2",
        "id:object-audit",
        "store:audit:object/updated",
    ]
    assert trace.events.count("transaction:enter") == trace.events.count("transaction:exit") == 2


@pytest.mark.parametrize("failure", ["id:object-revision", "clock:1", "digest", "validate:object"])
def test_revise_same_exception_and_no_later_calls(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    service, trace = harness(monkeypatch, fail=failure)
    current = existing()
    before = current.model_dump(mode="python")
    with pytest.raises(RuntimeError) as caught:
        service.revise(
            current,
            updates={"purpose_statement": "updated"},
            event_type="object/updated",
            evidence_refs=("e",),
        )
    assert caught.value is trace.error and current.model_dump(mode="python") == before
    order = ["evidence:e", "id:object-revision", "clock:1", "digest", "validate:object"]
    assert timeline(trace) == order[: order.index(failure) + 1]
