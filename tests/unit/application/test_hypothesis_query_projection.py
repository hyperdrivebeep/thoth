"""Freeze query predicates, evaluation order and response identities on the original handler."""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import pytest
from pydantic import JsonValue, ValidationError

from thoth.application.commands.hypotheses_full import HypothesisHandlers


class Row:
    def __init__(self, label: str, events: list[str], **fields: Any) -> None:
        self.label, self.events, self.fields = label, events, fields
        self.error: RuntimeError | None = None

    def __getattr__(self, name: str) -> Any:
        self.events.append(self.label + "." + name)
        return self.fields[name]

    def model_dump(self, *, mode: str) -> dict[str, str]:
        assert mode == "json"
        self.events.append(self.label + ".dump")
        if self.error is not None:
            raise self.error
        return {"id": self.label}


def owner(
    monkeypatch: pytest.MonkeyPatch, events: list[str], **lists: tuple[Row, ...]
) -> HypothesisHandlers:
    handler = object.__new__(HypothesisHandlers)
    store = Mock()

    def read(name: str, result: tuple[Row, ...]) -> Any:
        def call(*args: Any) -> tuple[Row, ...]:
            events.append(name)
            return result

        return call

    for name, values in lists.items():
        setattr(store, name, read(name, values))
    monkeypatch.setattr(handler, "_store", store, raising=False)

    def summarize(item: Row) -> dict[str, str]:
        events.append(item.label + ".summary")
        return {"id": item.label}

    monkeypatch.setattr(HypothesisHandlers, "_summary", staticmethod(summarize))
    return handler


FIELDS = (
    ("object_id", "object_id", "object:1"),
    ("portfolio_id", "portfolio_id", "portfolio:1"),
    ("primary_intent", "primary_intent", "EXPLORATORY"),
    ("stage", "development_stage", "DRAFT"),
    ("appraisal", "empirical_appraisal", "UNASSESSED"),
    ("freshness", "freshness", "CURRENT"),
)


@pytest.mark.asyncio
@pytest.mark.parametrize("request_field,record_field,match", FIELDS)
async def test_list_each_optional_filter(
    monkeypatch: pytest.MonkeyPatch, request_field: str, record_field: str, match: str
) -> None:
    events: list[str] = []
    a = Row("a", events, **{record_field: match})
    b = Row("b", events, **{record_field: "different"})
    handler = owner(monkeypatch, events, list_hypotheses=(a, b))
    result = await handler.list({"project_id": "p", request_field: match})
    assert result == {"hypotheses": [{"id": "a"}], "next_cursor": None}
    assert events == ["list_hypotheses", "a." + record_field, "b." + record_field, "a.summary"]


@pytest.mark.asyncio
async def test_list_short_circuit_order_and_optional_none(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    a = Row("a", events, **{field: match for _, field, match in FIELDS})
    b = Row("b", events, object_id="other")
    handler = owner(monkeypatch, events, list_hypotheses=(a, b))
    result = await handler.list({"project_id": "p", **{field: match for field, _, match in FIELDS}})
    assert result["hypotheses"] == [{"id": "a"}]
    assert events == [
        "list_hypotheses",
        *["a." + field for _, field, _ in FIELDS],
        "b.object_id",
        "a.summary",
    ]
    events.clear()
    assert (await handler.list({"project_id": "p"}))["hypotheses"] == [{"id": "a"}, {"id": "b"}]
    assert events == ["list_hypotheses", "a.summary", "b.summary"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "summary", "edge"])
async def test_graph_order_ignored_list_filters_and_exception(
    monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    events: list[str] = []
    nodes = tuple(
        Row(
            label,
            events,
            object_id="o",
            portfolio_id="pf",
            primary_intent=intent,
            hypothesis_id=label,
        )
        for label, intent in (("a", "Z"), ("b", "EXPLORATORY"), ("c", "A"), ("d", "Z"), ("e", None))
    )
    skip = Row("skip", events, object_id="other")
    edge, later = Row("edge", events), Row("later", events)
    handler = owner(
        monkeypatch, events, list_hypotheses=(*nodes, skip), list_relations=(edge, later)
    )
    error = RuntimeError("graph failure")
    if failure == "summary":

        def fail(item: Row) -> dict[str, str]:
            events.append(item.label + ".summary")
            raise error

        monkeypatch.setattr(HypothesisHandlers, "_summary", staticmethod(fail))
    if failure == "edge":
        edge.error = error
    payload: dict[str, JsonValue] = {
        "project_id": "p",
        "object_id": "o",
        "portfolio_id": "pf",
        "stage": "ignored",
        "primary_intent": "ignored",
        "appraisal": "ignored",
        "freshness": "ignored",
    }
    prefix = [
        "list_hypotheses",
        *[f"{label}.{field}" for label in "abcde" for field in ("object_id", "portfolio_id")],
        "skip.object_id",
        "list_relations",
    ]
    if failure:
        with pytest.raises(RuntimeError) as caught:
            await handler.graph_read(payload)
        assert caught.value is error
        assert events == prefix + (
            ["a.summary"]
            if failure == "summary"
            else [*[x + ".summary" for x in "abcde"], "edge.dump"]
        )
    else:
        result = await handler.graph_read(payload)
        assert list(result) == ["nodes", "edges", "alternative_families", "unknown_reserve"]
        assert result == {
            "nodes": [{"id": x} for x in "abcde"],
            "edges": [{"id": "edge"}, {"id": "later"}],
            "alternative_families": ["A", "Z"],
            "unknown_reserve": ["b"],
        }
        expected = [*prefix, *[x + ".summary" for x in "abcde"], "edge.dump", "later.dump"]
        assert events[: len(expected)] == expected
        assert a_fields(nodes) == ["Z", "EXPLORATORY", "A", "Z", None]


def a_fields(nodes: tuple[Row, ...]) -> list[Any]:
    return [row.fields["primary_intent"] for row in nodes]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,expected",
    [
        ("list", {"hypotheses": [], "next_cursor": None}),
        (
            "graph_read",
            {"nodes": [], "edges": [], "alternative_families": [], "unknown_reserve": []},
        ),
        ("relation_list", {"relations": []}),
        ("prediction_list", {"predictions": []}),
    ],
)
async def test_empty_query_results(
    monkeypatch: pytest.MonkeyPatch, method: str, expected: dict[str, Any]
) -> None:
    events: list[str] = []
    handler = owner(monkeypatch, events, list_hypotheses=(), list_relations=(), list_predictions=())
    assert await getattr(handler, method)({"project_id": "p"}) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "identifier,kind,expected",
    [
        (None, None, ["a", "b"]),
        ("source", None, ["a"]),
        ("target", None, ["a"]),
        ("absent", None, []),
        (None, "SUPPORTS", ["a"]),
        ("target", "CONTRADICTS", []),
    ],
)
async def test_relation_both_ends_and_type(
    monkeypatch: pytest.MonkeyPatch, identifier: str | None, kind: str | None, expected: list[str]
) -> None:
    events: list[str] = []
    rows = (
        Row(
            "a",
            events,
            source_hypothesis_id="source",
            target_hypothesis_id="target",
            relation_type="SUPPORTS",
        ),
        Row(
            "b",
            events,
            source_hypothesis_id="other",
            target_hypothesis_id="other",
            relation_type="CONTRADICTS",
        ),
    )
    handler = owner(monkeypatch, events, list_relations=rows)
    result = await handler.relation_list(
        {"project_id": "p", "hypothesis_id": identifier, "relation_type": kind}
    )
    assert result == {"relations": [{"id": x} for x in expected]}
    if identifier == "absent":
        assert not any(e.endswith("relation_type") or e.endswith("dump") for e in events)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state,bound,expected",
    [
        (None, None, ["a", "b", "c"]),
        (None, False, ["a", "c"]),
        (None, True, ["b"]),
        ("SEALED", None, ["a", "b"]),
        ("SEALED", False, ["a"]),
        ("SEALED", True, ["b"]),
    ],
)
async def test_prediction_none_false_and_state_short_circuit(
    monkeypatch: pytest.MonkeyPatch, state: str | None, bound: bool | None, expected: list[str]
) -> None:
    events: list[str] = []
    rows = tuple(
        Row(label, events, prespecification_state=s, observation_bound=b)
        for label, s, b in (("a", "SEALED", False), ("b", "SEALED", True), ("c", "OTHER", False))
    )
    handler = owner(monkeypatch, events, list_predictions=rows)
    result = await handler.prediction_list(
        {"project_id": "p", "prespecification_state": state, "observation_bound": bound}
    )
    assert result == {"predictions": [{"id": x} for x in expected]}
    if state == "SEALED":
        assert "c.observation_bound" not in events


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_quality_identity_strings_input_and_read_error(
    monkeypatch: pytest.MonkeyPatch, failure: bool
) -> None:
    events: list[str] = []
    gateway, quality = {"intent": "INCOMPLETE"}, {"clarity": "NOT_ASSESSED"}
    row = Row(
        "item", events, gateway_results=gateway, quality_profile=quality, evidence_refs=("b", "a")
    )
    handler = owner(monkeypatch, events)
    error = RuntimeError("read failure")

    def read(request: Any) -> Row:
        events.append("read")
        assert request.hypothesis_id == "h"
        if failure:
            raise error
        return row

    monkeypatch.setattr(handler, "_read", read)
    if failure:
        with pytest.raises(RuntimeError) as caught:
            await handler.quality_read({"project_id": "p", "hypothesis_id": "h"})
        assert caught.value is error and events == ["read"]
    else:
        result = await handler.quality_read({"project_id": "p", "hypothesis_id": "h"})
        assert events == [
            "read",
            "item.gateway_results",
            "item.quality_profile",
            "item.evidence_refs",
        ]
        assert list(result) == [
            "gateway_results",
            "epistemic_profile",
            "evaluator_identity",
            "rationale",
            "source_refs",
        ]
        assert result["gateway_results"] is gateway and result["epistemic_profile"] is quality
        assert result["source_refs"] == ["b", "a"] and row.fields["evidence_refs"] == ("b", "a")
        assert result["evaluator_identity"] == "deterministic-gateway+candidate-quality-profile:1"
        assert result["rationale"] == "axes remain non-compensatory and are never averaged"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method", ["list", "graph_read", "relation_list", "prediction_list", "quality_read"]
)
async def test_validation_precedes_store_or_projection(
    monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    events: list[str] = []
    handler = owner(monkeypatch, events)
    with pytest.raises(ValidationError):
        await getattr(handler, method)({"project_id": ""})
    assert events == []
