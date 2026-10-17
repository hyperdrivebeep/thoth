"""Freeze proposal bytes, identities, preview semantics and audit ordering before extraction."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from thoth.application.commands.hypotheses_full import HypothesisHandlers
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode

DIGESTS = {
    "split": "076a6eb7ee2c63a31b0101392dbfd7d6f5a49df491b3a4d152fcc9b494b22140",
    "merge_same": "3ecf24e87f52c142e48100113338d8ace5ef91271d906876f9a9c2be0509de83",
    "merge_different": "dc47fda7ee2dbb61fc47cc6d10f305e0da8123958f72824fc5fbd87f801d9d00",
}


class Item:
    def __init__(self, events: list[str], identifier: str, digest: str, statement: str) -> None:
        self.events, self.identifier = events, identifier
        self.fields = {
            "hypothesis_id": identifier,
            "revision_digest": digest,
            "statement": statement,
            "portfolio_id": "pf",
        }

    def __getattr__(self, name: str) -> str:
        self.events.append(self.identifier + "." + name)
        return self.fields[name]


class Store:
    def __init__(self, events: list[str], rows: dict[str, Item | None]) -> None:
        self.events, self.rows = events, rows
        self.error: RuntimeError | None = None

    def read_hypothesis(self, project: str, identifier: str, revision: str | None) -> Item | None:
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
        "h1": Item(events, "h1", "a" * 64, "same"),
        "h2": Item(events, "h2", "b" * 64, "different" if kind == "merge_different" else "same"),
    }
    store, service = Store(events, rows), Service(events)
    handler = object.__new__(HypothesisHandlers)
    monkeypatch.setattr(handler, "_store", store, raising=False)
    monkeypatch.setattr(handler, "_service", service, raising=False)
    payload: dict[str, Any] = {
        "project_id": "p",
        "evidence_refs": ["e2", "e1"],
        "rationale": "preview",
    }
    if kind == "split":
        payload.update(
            hypothesis_id="h1",
            expected_revision_digest="a" * 64,
            subhypotheses=[{"statement": "left"}, {"statement": "right"}],
        )
    else:
        payload.update(
            hypothesis_ids=["h1", "h2"],
            expected_revision_digests=["a" * 64, "b" * 64],
            field_mapping={"statement": "retain"},
        )
    return handler, payload, events, store, service


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["split", "merge_same", "merge_different"])
async def test_proposal_bytes_order_identity_and_repeat_digest(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    handler, payload, events, _, service = fixture(monkeypatch, kind)
    original = deepcopy(payload)
    method = "split_propose" if kind == "split" else "merge_propose"
    result = await getattr(handler, method)(payload)
    proposal = result["split_proposal" if kind == "split" else "merge_proposal"]
    assert proposal["digest"] == DIGESTS[kind]
    assert proposal["applied"] is False and proposal["owner_namespace"] == "REVISION"
    assert payload == original
    assert len(service.calls) == 1
    assert service.calls[0][:3] == (
        "p",
        "h1",
        "hypothesis/splitProposed" if kind == "split" else "hypothesis/mergeProposed",
    )
    assert service.calls[0][3] == proposal
    if kind == "split":
        keys = [
            "project_id",
            "hypothesis_id",
            "subhypotheses",
            "evidence_refs",
            "rationale",
            "expected_revision_digest",
            "applied",
            "owner_namespace",
            "digest",
        ]
        assert proposal["subhypotheses"] == [{"statement": "left"}, {"statement": "right"}]
        assert proposal["subhypotheses"] is service.calls[0][3]["subhypotheses"]
        assert result["lineage_preview"] == ["SPECIALIZES:h1"]
        assert events == [
            "read:h1",
            "h1.revision_digest",
            "h1.hypothesis_id",
            "audit",
            "h1.hypothesis_id",
            "h1.portfolio_id",
        ]
    else:
        keys = [
            "project_id",
            "hypothesis_ids",
            "field_mapping",
            "evidence_refs",
            "rationale",
            "expected_revision_digests",
            "semantic_conflicts",
            "applied",
            "owner_namespace",
            "digest",
        ]
        assert proposal["hypothesis_ids"] == ["h1", "h2"]
        assert proposal["expected_revision_digests"] == ["a" * 64, "b" * 64]
        assert proposal["semantic_conflicts"] == (
            ["statement"] if kind == "merge_different" else []
        )
        assert proposal["field_mapping"] is service.calls[0][3]["field_mapping"]
        assert result["lineage_preview"] == ["SUPERSEDES:h1", "SUPERSEDES:h2"]
        assert events == [
            "read:h1",
            "read:h2",
            "h1.revision_digest",
            "h2.revision_digest",
            "h1.statement",
            "h2.statement",
            "h1.hypothesis_id",
            "audit",
            "h1.portfolio_id",
        ]
    assert proposal["evidence_refs"] == ["e2", "e1"]
    assert list(proposal) == keys
    assert list(service.calls[0][3]) == keys
    assert list(result) == [
        "split_proposal" if kind == "split" else "merge_proposal",
        "lineage_preview",
        "portfolio_impact_preview",
    ]
    assert result["portfolio_impact_preview"] == ["pf"]
    assert await getattr(handler, method)(payload) == result
    assert len(service.calls) == 2 and service.calls[1][3]["digest"] == DIGESTS[kind]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["split", "merge_same"])
async def test_audit_failure_keeps_exception_and_stops_preview(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    handler, payload, events, _, service = fixture(monkeypatch, kind)
    error = RuntimeError("audit failed")
    service.error = error
    with pytest.raises(RuntimeError) as caught:
        await getattr(handler, "split_propose" if kind == "split" else "merge_propose")(payload)
    assert caught.value is error
    assert events[-1] == "audit" and "h1.portfolio_id" not in events
    assert service.calls[0][3]["digest"] == DIGESTS[kind]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["split_missing", "split_stale", "merge_count", "merge_missing", "merge_stale"]
)
async def test_existing_guards_reject_before_proposal_audit(
    monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    kind = "split" if case.startswith("split") else "merge_same"
    handler, payload, events, store, service = fixture(monkeypatch, kind)
    expected = "hypothesis not found"
    if case == "split_missing":
        store.rows["h1"] = None
    elif case == "split_stale":
        payload["expected_revision_digest"] = "f" * 64
        expected = "hypothesis revision changed concurrently"
    elif case == "merge_count":
        payload["hypothesis_ids"] = ["h1", "h2", "h3"]
        expected = "hypothesis IDs and expected revision digests must align"
    elif case == "merge_missing":
        store.rows["h2"] = None
    else:
        payload["expected_revision_digests"] = ["a" * 64, "f" * 64]
        expected = "revision mismatch"
    with pytest.raises(RpcApplicationError) as caught:
        await getattr(handler, "split_propose" if kind == "split" else "merge_propose")(payload)
    assert caught.value.code == RpcErrorCode.DOMAIN_REJECTED and str(caught.value) == expected
    assert service.calls == [] and not any(e.endswith("statement") or e == "audit" for e in events)
    if case == "merge_count":
        assert events == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["split", "merge_same"])
async def test_store_exception_is_not_replaced_by_projection(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    handler, payload, events, store, service = fixture(monkeypatch, kind)
    store.error = RuntimeError("read failed")
    with pytest.raises(RuntimeError) as caught:
        await getattr(handler, "split_propose" if kind == "split" else "merge_propose")(payload)
    assert caught.value is store.error
    assert events == ["read:h1"] and service.calls == []
