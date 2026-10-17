"""Freeze relation/attention construction and the surrounding root ordering."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest
from tests.unit.services.test_object_record_drafts import (
    NOW,
    Trace,
    existing,
    timeline,
)
from tests.unit.services.test_object_record_drafts import (
    Store as BaseStore,
)
from tests.unit.services.test_object_record_drafts import (
    harness as base_harness,
)

from thoth.application.services import decision_object_service as module
from thoth.application.services.decision_object_service import DecisionObjectService
from thoth.domain.decision_object_full import ObjectAttentionRecord, ObjectRelationRecord


class Store(BaseStore):
    def __init__(
        self, trace: Trace, items: tuple[ObjectAttentionRecord, ...] = (), bad_profile: bool = False
    ) -> None:
        super().__init__(trace, False, bad_profile)
        self.items = list(items)
        self.relations: list[ObjectRelationRecord] = []
        self.attentions: list[ObjectAttentionRecord] = []
        self.audits: list[Any] = []

    def read_profile(self, reference: str, version: int | None) -> Any:
        assert version is None
        self.trace.hit("profile:" + reference)
        return (
            None
            if self.bad_profile
            else SimpleNamespace(
                profile_ref=reference, enabled=True, allowed_relation_types=("DEPENDS_ON",)
            )
        )

    def add_relation(self, record: ObjectRelationRecord) -> None:
        self.relations.append(record)
        self.trace.hit("store:relation")

    def list_attention(self, project: str, identifier: str) -> tuple[ObjectAttentionRecord, ...]:
        assert project == "p" and identifier == "object:old"
        self.trace.hit("attention:list")
        return tuple(self.items)

    def add_attention(self, record: ObjectAttentionRecord) -> None:
        self.attentions.append(record)
        self.items = [item for item in self.items if item.attention_id != record.attention_id] + [
            record
        ]
        self.trace.hit("store:attention")

    def append_audit(self, value: Any) -> None:
        self.audits.append(value)
        super().append_audit(value)


class Dependencies:
    def __init__(self, trace: Trace) -> None:
        self.trace = trace
        self.rows: list[Any] = []

    def add(self, value: Any) -> None:
        self.rows.append(value)
        self.trace.hit("dependency:add")


class Dump:
    def __init__(self, trace: Trace, label: str, record: Any) -> None:
        self.trace, self.label, self.record = trace, label, record

    def __getattr__(self, name: str) -> Any:
        return getattr(self.record, name)

    def model_dump(self, *, mode: str) -> dict[str, Any]:
        assert mode == "python"
        result = self.record.model_dump(mode=mode)
        self.trace.hit("dump:" + self.label)
        return result


def relation(active: bool = True) -> ObjectRelationRecord:
    return ObjectRelationRecord(
        relation_revision_id="rr:old",
        relation_id="r:old",
        project_id="p",
        source_object_id="object:old",
        relation_type="DEPENDS_ON",
        target_ref="CRITERION:x",
        semantic_role="gates",
        evidence_refs=("e",),
        actor_or_agent_ref="agent:unit",
        valid_from=NOW - timedelta(days=1),
        authority_state="OFFICIAL",
        active=active,
        revision_digest="b" * 64,
        created_at=NOW - timedelta(days=1),
    )


def attention(
    identifier: str = "target",
    state: str = "OPEN",
    condition: str = "condition",
    kind: str = "USER_INPUT_REQUIRED",
) -> ObjectAttentionRecord:
    return ObjectAttentionRecord(
        attention_revision_id="ar:" + identifier,
        attention_id=identifier,
        project_id="p",
        object_id="object:old",
        attention_type=kind,
        risk="MEDIUM",
        reason="reason",
        condition_ref=condition,
        state=state,
        revision_digest="c" * 64,
        created_at=NOW,
        acknowledged_by="human:old" if state == "ACKNOWLEDGED" else None,
    )


def fixture(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail: str | None = None,
    items: tuple[ObjectAttentionRecord, ...] = (),
    bad_profile: bool = False,
    bad_evidence: bool = False,
) -> tuple[Any, Trace, Store, dict[str, dict[str, object]]]:
    service, trace = base_harness(monkeypatch, fail=fail, bad_evidence=bad_evidence)
    store = Store(trace, items, bad_profile)
    cast(dict[str, Any], vars(service)).update(_store=store, _dependencies=Dependencies(trace))
    captured: dict[str, dict[str, object]] = {}
    for name in ("relation", "attention"):
        original = vars(DecisionObjectService)["_" + name + "_digest"].__func__

        def digest(
            data: dict[str, object], label: str = name, implementation: Any = original
        ) -> str:
            captured[label] = data
            trace.hit(label + ":digest")
            return implementation(data)

        monkeypatch.setattr(service, "_" + name + "_digest", digest)
    relation_validate, attention_validate = (
        ObjectRelationRecord.model_validate,
        ObjectAttentionRecord.model_validate,
    )

    class RelationCodec:
        @staticmethod
        def model_validate(data: dict[str, object]) -> ObjectRelationRecord:
            record = relation_validate(data)
            trace.hit("relation:validate")
            return record

    class AttentionCodec:
        @staticmethod
        def model_validate(data: dict[str, object]) -> ObjectAttentionRecord:
            record = attention_validate(data)
            trace.hit("attention:validate")
            return record

    monkeypatch.setattr(module, "ObjectRelationRecord", RelationCodec)
    monkeypatch.setattr(module, "ObjectAttentionRecord", AttentionCodec)
    return service, trace, store, captured


def add(service: Any, current: Any, kind: str = "DEPENDS_ON") -> Any:
    return service.add_relation(
        current,
        relation_type=kind,
        target_ref="CRITERION:x",
        semantic_role="gates",
        evidence_refs=("e",),
        authority_state="OFFICIAL",
        actor_ref="agent:unit",
    )


@pytest.mark.parametrize("profiles", [(), ("P1",)])
def test_add_now_identity_ids_order_and_dependency_before_revision(
    monkeypatch: pytest.MonkeyPatch, profiles: tuple[str, ...]
) -> None:
    service, trace, store, captured = fixture(monkeypatch)
    current = existing().model_copy(update={"profile_refs": profiles, "relation_refs": ("prior",)})
    before = current.model_dump(mode="python")
    record, revised, _ = add(service, current)
    assert record is store.relations[0]
    assert captured["relation"]["valid_from"] is captured["relation"]["created_at"]
    assert record.valid_from == record.created_at == NOW + timedelta(seconds=1)
    assert list(captured["relation"]) == [
        "relation_revision_id",
        "relation_id",
        "project_id",
        "source_object_id",
        "relation_type",
        "target_ref",
        "semantic_role",
        "evidence_refs",
        "actor_or_agent_ref",
        "valid_from",
        "authority_state",
        "active",
        "created_at",
    ]
    assert revised.relation_refs == ("prior", record.relation_id)
    assert current.model_dump(mode="python") == before
    assert timeline(trace) == [
        "evidence:e",
        *["profile:" + p for p in profiles],
        "clock:1",
        "id:object-relation-revision",
        "id:object-relation",
        "relation:digest",
        "relation:validate",
        "store:relation",
        "dependency:add",
        "evidence:e",
        "id:object-revision",
        "clock:2",
        "digest",
        "validate:object",
        "id:snapshot",
        "id:changeset",
        "commit",
        "store:object",
        "clock:3",
        "id:object-audit",
        "store:audit:object/relationChanged",
    ]
    edge = cast(dict[str, Any], vars(service))["_dependencies"].rows[0]
    assert (
        edge.relation_id == record.relation_revision_id and edge.source_ref == "OBJECT:object:old"
    )
    assert edge.target_ref == record.target_ref and edge.revision_digest == record.revision_digest


@pytest.mark.parametrize("case", ["evidence", "profile", "type"])
def test_add_guards_before_now_and_ids(monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    service, trace, store, _ = fixture(
        monkeypatch, bad_evidence=case == "evidence", bad_profile=case == "profile"
    )
    current = existing().model_copy(update={"profile_refs": ("P1",)})
    with pytest.raises(ValueError):
        add(service, current, "NOT_SUPPORTED" if case == "type" else "DEPENDS_ON")
    assert timeline(trace) == (
        ["evidence:e"] if case == "evidence" else ["evidence:e", "profile:P1"]
    )
    assert store.relations == []


def test_end_keeps_distinct_clock_calls_and_old_record(monkeypatch: pytest.MonkeyPatch) -> None:
    service, trace, store, captured = fixture(monkeypatch)
    current, original = existing(), relation()
    before = original.model_dump(mode="python")
    ended, revised, _ = service.end_relation(
        current, Dump(trace, "relation", original), reason="end", evidence_refs=("e",)
    )
    assert ended is store.relations[0] and ended.active is False
    assert ended.valid_to == NOW + timedelta(seconds=1)
    assert ended.created_at == NOW + timedelta(seconds=2) and ended.valid_to != ended.created_at
    assert (
        ended.valid_from == original.valid_from
        and ended.supersedes_revision_digest == original.revision_digest
    )
    assert (
        original.model_dump(mode="python") == before
        and "revision_digest" not in captured["relation"]
    )
    assert revised.relation_refs == current.relation_refs
    assert timeline(trace) == [
        "dump:relation",
        "id:object-relation-revision",
        "clock:1",
        "clock:2",
        "relation:digest",
        "relation:validate",
        "store:relation",
        "evidence:e",
        "id:object-revision",
        "clock:3",
        "digest",
        "validate:object",
        "id:snapshot",
        "id:changeset",
        "commit",
        "store:object",
        "clock:4",
        "id:object-audit",
        "store:audit:object/relationChanged",
        "clock:5",
        "id:object-audit",
        "store:audit:object/relationChanged",
    ]
    assert store.audits[-1].payload == {"reason": "end"}


def test_end_inactive_guard_precedes_dump_and_ports(monkeypatch: pytest.MonkeyPatch) -> None:
    service, trace, _, _ = fixture(monkeypatch)
    with pytest.raises(ValueError):
        service.end_relation(
            existing(), Dump(trace, "relation", relation(False)), reason="end", evidence_refs=("e",)
        )
    assert timeline(trace) == []


def raise_item(service: Any, current: Any) -> Any:
    return service.raise_attention(
        current,
        attention_type="USER_INPUT_REQUIRED",
        risk="MEDIUM",
        reason="reason",
        condition_ref="condition",
    )


@pytest.mark.parametrize("state", ["OPEN", "ACKNOWLEDGED"])
def test_raise_existing_returns_same_object_without_ids_clock_or_writes(
    monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    old = attention(state=state)
    service, trace, store, _ = fixture(monkeypatch, items=(old,))
    assert raise_item(service, existing()) is old
    assert timeline(trace) == ["attention:list"] and store.attentions == store.audits == []
    state_values = cast(dict[str, Any], vars(service))
    assert state_values["_ids"].count == state_values["_clock"].count == 0


@pytest.mark.parametrize("case", ["empty", "cleared", "different_condition"])
def test_raise_new_order_values_and_input_preservation(
    monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    items = (
        ()
        if case == "empty"
        else (
            attention(
                state="CLEARED" if case == "cleared" else "OPEN",
                condition="other" if case == "different_condition" else "condition",
            ),
        )
    )
    before = [r.model_dump(mode="python") for r in items]
    service, trace, store, captured = fixture(monkeypatch, items=items)
    record = raise_item(service, existing())
    assert (
        record is store.attentions[0]
        and record.state == "OPEN"
        and record.created_at == NOW + timedelta(seconds=1)
    )
    assert [r.model_dump(mode="python") for r in items] == before
    assert list(captured["attention"]) == [
        "attention_revision_id",
        "attention_id",
        "project_id",
        "object_id",
        "attention_type",
        "risk",
        "reason",
        "condition_ref",
        "state",
        "created_at",
    ]
    assert timeline(trace) == [
        "attention:list",
        "id:object-attention-revision",
        "id:object-attention",
        "clock:1",
        "attention:digest",
        "attention:validate",
        "store:attention",
        "clock:2",
        "id:object-audit",
        "store:audit:object/attentionRaised",
    ]
    assert trace.objects == trace.commits == []


@pytest.mark.parametrize("resolved", [False, True])
@pytest.mark.parametrize("note", [None, "acknowledged"])
@pytest.mark.parametrize("others", [0, 1, 2])
def test_ack_resolved_others_and_event_remain_root(
    monkeypatch: pytest.MonkeyPatch, resolved: bool, note: str | None, others: int
) -> None:
    old = attention()
    additional = tuple(
        attention("other:" + str(i), condition="other", kind="OTHER_" + str(i))
        for i in range(others)
    )
    service, trace, store, captured = fixture(
        monkeypatch, items=(old, attention("ignored", "CLEARED"), *additional)
    )
    current = existing().model_copy(
        update={"blockers": () if resolved else ("condition",), "attention": "USER_INPUT_REQUIRED"}
    )
    before = old.model_dump(mode="python")
    updated, revised, _ = service.acknowledge_attention(
        current, Dump(trace, "attention", old), actor_ref="human:unit", note=note
    )
    assert updated is store.attentions[0] and updated.state == (
        "CLEARED" if resolved else "ACKNOWLEDGED"
    )
    assert updated.acknowledged_by == "human:unit" and updated.acknowledgement_note == note
    assert (
        updated.supersedes_revision_digest == old.revision_digest
        and updated.created_at == NOW + timedelta(seconds=1)
    )
    assert (
        old.model_dump(mode="python") == before and "revision_digest" not in captured["attention"]
    )
    assert revised.attention == (
        "OTHER_0" if others else "NONE" if resolved else "USER_INPUT_REQUIRED"
    )
    event_type = "object/attentionCleared" if resolved else "object/updated"
    assert timeline(trace) == [
        "dump:attention",
        "id:object-attention-revision",
        "clock:1",
        "attention:digest",
        "attention:validate",
        "store:attention",
        "attention:list",
        "id:object-revision",
        "clock:2",
        "digest",
        "validate:object",
        "id:snapshot",
        "id:changeset",
        "commit",
        "store:object",
        "clock:3",
        "id:object-audit",
        "store:audit:" + event_type,
    ]


PREFIX = {
    "add": [
        "evidence:e",
        "profile:P1",
        "clock:1",
        "id:object-relation-revision",
        "id:object-relation",
        "relation:digest",
        "relation:validate",
        "store:relation",
        "dependency:add",
    ],
    "end": [
        "dump:relation",
        "id:object-relation-revision",
        "clock:1",
        "clock:2",
        "relation:digest",
        "relation:validate",
        "store:relation",
    ],
    "raise": [
        "attention:list",
        "id:object-attention-revision",
        "id:object-attention",
        "clock:1",
        "attention:digest",
        "attention:validate",
        "store:attention",
    ],
    "ack": [
        "dump:attention",
        "id:object-attention-revision",
        "clock:1",
        "attention:digest",
        "attention:validate",
        "store:attention",
        "attention:list",
    ],
}


@pytest.mark.parametrize(
    "kind,failure", [(kind, point) for kind, points in PREFIX.items() for point in points]
)
def test_draft_and_boundary_failures_keep_exception_and_order(
    monkeypatch: pytest.MonkeyPatch, kind: str, failure: str
) -> None:
    service, trace, _, _ = fixture(monkeypatch, fail=failure)
    current = existing().model_copy(update={"profile_refs": ("P1",)})
    with pytest.raises(RuntimeError) as caught:
        if kind == "add":
            add(service, current)
        elif kind == "end":
            service.end_relation(
                current, Dump(trace, "relation", relation()), reason="end", evidence_refs=("e",)
            )
        elif kind == "raise":
            raise_item(service, current)
        else:
            service.acknowledge_attention(
                current, Dump(trace, "attention", attention()), actor_ref="human:unit", note=None
            )
    assert caught.value is trace.error
    assert timeline(trace) == PREFIX[kind][: PREFIX[kind].index(failure) + 1]
