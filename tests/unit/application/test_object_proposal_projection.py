"""Freeze Object proposal payloads, conflicts, digests and audit sequencing before moving them."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from thoth.application.commands.objects_full import DecisionObjectHandlers
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode

DIGESTS = {
    "split": "9b0f65467b6a053bc14f807fd9dce53bb1ab5a46577a78c5f03fae82a608c8e9",
    "merge_same": "85680fb22a7025975b64a55fa29a2127f84439be48e3168880389a1cd01cf395",
    "merge_different": "d9c79b0231d8d66eae5f16287d97fb3ff3df1d2149e9a050824edaed1b248c56",
    "merge_duplicate_conflicts": "d320ed18f6ef081265ea7cebe173f53b3e32b9e7bd11b1edbec9a193a56931d3",
}


class Item:
    def __init__(self, events: list[str], identifier: str, digest: str, frame: str) -> None:
        self.events, self.identifier = events, identifier
        self.fields = {"object_id": identifier, "revision_digest": digest, "problem_frame": frame}

    def __getattr__(self, name: str) -> str:
        self.events.append(self.identifier + "." + name)
        return self.fields[name]


class Store:
    def __init__(self, events: list[str], rows: dict[str, Item | None]) -> None:
        self.events, self.rows = events, rows
        self.error: RuntimeError | None = None

    def read_object(self, project: str, identifier: str, revision: str | None) -> Item | None:
        assert project == "p" and revision is None
        self.events.append("read:" + identifier)
        if self.error is not None:
            raise self.error
        return self.rows[identifier]

    def __getattr__(self, name: str) -> Any:
        raise AssertionError("Unexpected store method: " + name)


class Service:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls: list[tuple[Any, ...]] = []
        self.error: RuntimeError | None = None

    def audit(self, *args: Any) -> None:
        self.events.append("audit")
        self.calls.append(args)
        if self.error is not None:
            raise self.error

    def __getattr__(self, name: str) -> Any:
        raise AssertionError("Unexpected service method: " + name)


def fixture(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> tuple[Any, dict[str, Any], list[str], Store, Service]:
    events: list[str] = []
    rows: dict[str, Item | None] = {
        "o1": Item(events, "o1", "a" * 64, "same"),
        "o2": Item(events, "o2", "b" * 64, "same" if kind == "merge_same" else "different"),
        "o3": Item(events, "o3", "c" * 64, "different"),
    }
    store, service = Store(events, rows), Service(events)
    handler = object.__new__(DecisionObjectHandlers)
    monkeypatch.setattr(handler, "_store", store, raising=False)
    monkeypatch.setattr(handler, "_service", service, raising=False)
    payload: dict[str, Any] = {
        "project_id": "p",
        "evidence_refs": ["e2", "e1"],
        "rationale": "preview",
    }
    if kind == "split":
        payload.update(
            object_id="o1",
            expected_revision_digest="a" * 64,
            partitions=[{"purpose": "left"}, {"purpose": "right"}],
        )
    else:
        payload.update(
            object_ids=["o1", "o2"],
            expected_revision_digests=["a" * 64, "b" * 64],
            field_mapping={"purpose": "retain"},
        )
        if kind == "merge_duplicate_conflicts":
            payload["object_ids"].append("o3")
            payload["expected_revision_digests"].append("c" * 64)
    return handler, payload, events, store, service


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", tuple(DIGESTS))
async def test_proposal_keys_identity_conflicts_and_fixed_digest(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    handler, payload, events, store, service = fixture(monkeypatch, kind)
    original = deepcopy(payload)
    method = "split_propose" if kind == "split" else "merge_propose"
    result = await getattr(handler, method)(payload)
    proposal = result["split_proposal" if kind == "split" else "merge_proposal"]
    assert proposal["digest"] == DIGESTS[kind]
    assert proposal["applied"] is False and proposal["owner_namespace"] == "REVISION"
    assert proposal["evidence_refs"] == ["e2", "e1"] and payload == original
    assert len(service.calls) == 1 and service.calls[0][0] is store.rows["o1"]
    assert service.calls[0][1] == (
        "object/splitProposed" if kind == "split" else "object/mergeProposed"
    )
    assert service.calls[0][2] == proposal
    if kind == "split":
        keys = [
            "project_id",
            "object_id",
            "partitions",
            "evidence_refs",
            "rationale",
            "expected_revision_digest",
            "applied",
            "owner_namespace",
            "digest",
        ]
        assert proposal["partitions"] == [{"purpose": "left"}, {"purpose": "right"}]
        assert proposal["partitions"] is service.calls[0][2]["partitions"]
        assert result["lineage_preview"] == ["PART_OF:o1"]
        assert result["impact_preview"] == ["dependent projections become stale after application"]
        assert events == ["read:o1", "o1.revision_digest", "audit"]
    else:
        keys = [
            "project_id",
            "object_ids",
            "field_mapping",
            "evidence_refs",
            "rationale",
            "expected_revision_digests",
            "applied",
            "owner_namespace",
            "conflicts",
            "digest",
        ]
        identifiers = ["o1", "o2", "o3"] if kind == "merge_duplicate_conflicts" else ["o1", "o2"]
        assert proposal["object_ids"] == identifiers
        assert proposal["expected_revision_digests"] == original["expected_revision_digests"]
        assert proposal["conflicts"] == ([] if kind == "merge_same" else ["problem_frame"])
        assert proposal["field_mapping"] is service.calls[0][2]["field_mapping"]
        assert result["lineage_preview"] == [
            "SUPERSEDES:" + identifier for identifier in identifiers
        ]
        assert result["impact_preview"] == ["application belongs to Revision namespace"]
        assert events == [
            *["read:" + identifier for identifier in identifiers],
            *[identifier + ".revision_digest" for identifier in identifiers],
            *[
                field
                for identifier in identifiers[1:]
                for field in (identifier + ".problem_frame", "o1.problem_frame")
            ],
            "audit",
        ]
    assert list(proposal) == keys and list(service.calls[0][2]) == keys
    assert list(result) == [
        "split_proposal" if kind == "split" else "merge_proposal",
        "lineage_preview",
        "impact_preview",
    ]
    assert await getattr(handler, method)(payload) == result
    assert len(service.calls) == 2 and service.calls[1][2]["digest"] == DIGESTS[kind]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["split", "merge_same"])
async def test_audit_exception_stops_result_and_keeps_identity(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    handler, payload, events, _, service = fixture(monkeypatch, kind)
    error = RuntimeError("audit failed")
    service.error = error
    with pytest.raises(RuntimeError) as caught:
        await getattr(handler, "split_propose" if kind == "split" else "merge_propose")(payload)
    assert caught.value is error and events[-1] == "audit"
    assert len(service.calls) == 1 and service.calls[0][2]["digest"] == DIGESTS[kind]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "split_missing",
        "split_stale",
        "merge_count",
        "merge_missing",
        "merge_first_missing",
        "merge_stale",
    ],
)
async def test_count_read_none_and_revision_guards_precede_audit(
    monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    kind = "split" if case.startswith("split") else "merge_same"
    handler, payload, events, store, service = fixture(monkeypatch, kind)
    expected = "object not found" if kind == "split" else "merge object not found"
    if case in ("split_missing", "merge_first_missing"):
        store.rows["o1"] = None
    elif case == "split_stale":
        payload["expected_revision_digest"] = "f" * 64
        expected = "object revision changed concurrently"
    elif case == "merge_count":
        payload["object_ids"] = ["o1", "o2", "o3"]
        expected = "object IDs and expected revision digests must align"
    elif case == "merge_missing":
        store.rows["o2"] = None
    else:
        payload["expected_revision_digests"] = ["a" * 64, "f" * 64]
        expected = "merge revision mismatch"
    with pytest.raises(RpcApplicationError) as caught:
        await getattr(handler, "split_propose" if kind == "split" else "merge_propose")(payload)
    assert caught.value.code == RpcErrorCode.DOMAIN_REJECTED and str(caught.value) == expected
    assert service.calls == [] and not any(
        e.endswith("problem_frame") or e == "audit" for e in events
    )
    if case == "merge_count":
        assert events == []
    if case in ("merge_missing", "merge_first_missing"):
        assert events == ["read:o1", "read:o2"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["split", "merge_same"])
async def test_store_exception_never_enters_payload_or_audit(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    handler, payload, events, store, service = fixture(monkeypatch, kind)
    store.error = RuntimeError("read failed")
    with pytest.raises(RuntimeError) as caught:
        await getattr(handler, "split_propose" if kind == "split" else "merge_propose")(payload)
    assert caught.value is store.error and events == ["read:o1"] and service.calls == []
