"""Freeze detailed read order and failures at the original handler boundary."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from thoth.application.commands.hypotheses_full import HypothesisHandlers
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


class Trace:
    def __init__(self, failure: str | None = None) -> None:
        self.events: list[str] = []
        self.failure = failure
        self.error = RuntimeError("detail query failure")

    def tick(self, name: str) -> None:
        self.events.append(name)
        if name == self.failure:
            raise self.error


class Row:
    def __init__(self, trace: Trace, name: str, **fields: Any) -> None:
        self.trace, self.name, self.fields = trace, name, fields

    def __getattr__(self, name: str) -> Any:
        return self.fields[name]

    def model_dump(self, *, mode: str) -> dict[str, str]:
        assert mode == "json"
        self.trace.tick("dump:" + self.name)
        return {"id": self.name}


class Store:
    def __init__(
        self, trace: Trace, results: dict[str, Any], args: dict[str, tuple[Any, ...]]
    ) -> None:
        self.trace, self.results, self.args = trace, results, args

    def __getattr__(self, name: str) -> Any:
        assert name in self.results, "Unexpected read, write or transaction: " + name

        def call(*args: Any) -> Any:
            assert args == self.args[name]
            self.trace.tick("store:" + name)
            return self.results[name]

        return call


ORDER = {
    "read": [
        "root:read",
        "dump:h",
        "store:list_assumptions",
        "dump:a",
        "dump:b",
        "store:list_predictions",
        "dump:prediction",
        "store:list_appraisals",
        "dump:appraisal1",
        "dump:appraisal2",
    ],
    "portfolio_list": ["store:list_portfolios", "dump:portfolio1"],
    "portfolio_read": [
        "root:portfolio",
        "dump:portfolio1",
        "store:list_relations",
        "dump:relation1",
        "dump:relation2",
    ],
    "prediction_read": [
        "store:read_prediction",
        "dump:prediction",
        "store:list_test_bindings",
        "dump:binding1",
        "dump:binding2",
        "dump:binding3",
    ],
    "assumption_list": ["store:list_assumptions", "store:read_prediction", "dump:b"],
    "appraisal_read": [
        "root:read",
        "store:list_appraisals",
        "store:list_test_bindings",
        "dump:binding1",
        "dump:binding2",
        "dump:appraisal1",
        "dump:appraisal2",
    ],
    "audit_read": ["root:read", "store:list_audit", "dump:audit1", "dump:audit2"],
}


def fixture(
    monkeypatch: pytest.MonkeyPatch, method: str, failure: str | None = None
) -> tuple[Any, dict[str, Any], Trace, Store]:
    trace = Trace(failure)

    def row(name: str, **fields: Any) -> Row:
        return Row(trace, name, **fields)

    item = row("h", revision_digest="a" * 64, empirical_appraisal="INCONCLUSIVE")
    portfolio = row("portfolio1", object_id="o")
    prediction = row("prediction", project_id="p", assumption_refs=("b", "a"))
    results: dict[str, Any] = {
        "list_assumptions": (
            row("a", assumption_id="a", status="UNVALIDATED"),
            row("b", assumption_id="b", status="VALIDATED"),
        ),
        "list_predictions": (prediction,),
        "list_appraisals": (
            row("appraisal1", evidence_refs=("z", "a"), test_assessment_refs=("binding2",)),
            row(
                "appraisal2",
                evidence_refs=("a", "b"),
                test_assessment_refs=("binding1", "binding2"),
            ),
        ),
        "list_portfolios": (portfolio, row("portfolio2", object_id="other")),
        "list_relations": (row("relation1"), row("relation2")),
        "read_prediction": prediction,
        "list_test_bindings": tuple(
            row("binding" + str(i), test_binding_id="binding" + str(i)) for i in (1, 2, 3)
        ),
        "list_audit": (row("audit1"), row("audit2")),
    }
    args: dict[str, tuple[Any, ...]] = {name: ("p", "h") for name in results}
    args.update(
        list_portfolios=("p",),
        list_relations=("p", "pf"),
        read_prediction=("prediction:1",),
        list_test_bindings=("p", "prediction:1" if method == "prediction_read" else None),
    )
    store = Store(trace, results, args)
    handler = object.__new__(HypothesisHandlers)
    monkeypatch.setattr(handler, "_store", store, raising=False)

    def read(request: Any) -> Row:
        assert request.project_id == "p" and request.hypothesis_id == "h"
        trace.tick("root:read")
        return item

    def read_portfolio(request: Any) -> Row:
        assert request.project_id == "p" and request.portfolio_id == "pf"
        trace.tick("root:portfolio")
        return portfolio

    monkeypatch.setattr(handler, "_read", read)
    monkeypatch.setattr(handler, "_portfolio", read_portfolio)
    payload: dict[str, Any] = {"project_id": "p", "hypothesis_id": "h"}
    if method == "portfolio_list":
        payload = {"project_id": "p", "object_id": "o", "stage": "ignored"}
    elif method == "portfolio_read":
        payload = {"project_id": "p", "portfolio_id": "pf"}
    elif method == "prediction_read":
        payload = {"project_id": "p", "prediction_id": "prediction:1"}
    elif method == "assumption_list":
        payload.update(prediction_id="prediction:1", status="VALIDATED")
    return handler, payload, trace, store


@pytest.mark.asyncio
@pytest.mark.parametrize("method", tuple(ORDER))
async def test_detail_reads_exact_order_values_and_dump_mode(
    monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    handler, payload, trace, _ = fixture(monkeypatch, method)
    result = await getattr(handler, method)(payload)
    assert trace.events == ORDER[method]
    expected = {
        "read": {
            "hypothesis": {"id": "h"},
            "assumptions": [{"id": "a"}, {"id": "b"}],
            "predictions": [{"id": "prediction"}],
            "appraisals": [{"id": "appraisal1"}, {"id": "appraisal2"}],
        },
        "portfolio_list": {"portfolios": [{"id": "portfolio1"}], "next_cursor": None},
        "portfolio_read": {
            "portfolio": {"id": "portfolio1"},
            "relations": [{"id": "relation1"}, {"id": "relation2"}],
        },
        "prediction_read": {
            "prediction": {"id": "prediction"},
            "test_bindings": [{"id": "binding1"}, {"id": "binding2"}, {"id": "binding3"}],
        },
        "assumption_list": {"assumptions": [{"id": "b"}]},
        "appraisal_read": {
            "hypothesis_revision_digest": "a" * 64,
            "cumulative_evidence": ["z", "a", "b"],
            "test_validity_chain": [{"id": "binding1"}, {"id": "binding2"}],
            "appraisals": [{"id": "appraisal1"}, {"id": "appraisal2"}],
            "current_scoped_appraisal": "INCONCLUSIVE",
        },
        "audit_read": {"records": [{"id": "audit1"}, {"id": "audit2"}]},
    }[method]
    assert result == expected and list(result) == list(expected)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,failure", [(m, event) for m, events in ORDER.items() for event in events]
)
async def test_each_read_or_dump_failure_stops_in_original_order(
    monkeypatch: pytest.MonkeyPatch, method: str, failure: str
) -> None:
    handler, payload, trace, _ = fixture(monkeypatch, method, failure)
    with pytest.raises(RuntimeError) as caught:
        await getattr(handler, method)(payload)
    assert caught.value is trace.error
    assert trace.events == ORDER[method][: ORDER[method].index(failure) + 1]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["missing", "other_project"])
async def test_prediction_guard_precedes_detail_reads(
    monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    handler, payload, trace, store = fixture(monkeypatch, "prediction_read")
    store.results["read_prediction"] = (
        None if case == "missing" else Row(trace, "other", project_id="other")
    )
    with pytest.raises(RpcApplicationError) as caught:
        await handler.prediction_read(payload)
    assert caught.value.code == RpcErrorCode.DOMAIN_REJECTED
    assert str(caught.value) == "prediction not found"
    assert trace.events == ["store:read_prediction"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["missing_prediction", "no_optional_filters", "empty", "no_status_match"]
)
async def test_assumption_optional_and_empty_results(
    monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    handler, payload, trace, store = fixture(monkeypatch, "assumption_list")
    if case == "missing_prediction":
        store.results["read_prediction"] = None
    elif case == "no_optional_filters":
        payload.pop("prediction_id")
        payload.pop("status")
    elif case == "empty":
        store.results["list_assumptions"] = ()
    else:
        payload["status"] = "ABSENT"
    result = await handler.assumption_list(payload)
    assert result == {
        "assumptions": [{"id": "a"}, {"id": "b"}] if case == "no_optional_filters" else []
    }
    if case == "no_optional_filters":
        assert trace.events == ["store:list_assumptions", "dump:a", "dump:b"]
    else:
        assert trace.events == ["store:list_assumptions", "store:read_prediction"]


@pytest.mark.asyncio
@pytest.mark.parametrize("method", tuple(ORDER))
async def test_invalid_input_performs_no_read_or_dump(
    monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    handler, payload, trace, _ = fixture(monkeypatch, method)
    payload["project_id"] = ""
    with pytest.raises(ValidationError):
        await getattr(handler, method)(payload)
    assert trace.events == []
