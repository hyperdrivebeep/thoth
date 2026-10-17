"""Freeze mutation result order, exception boundaries and payload identities on the root."""

from __future__ import annotations

from copy import deepcopy
from itertools import product
from typing import Any

import pytest

from thoth.application.commands import hypotheses_full as module
from thoth.application.commands.hypotheses_full import HypothesisHandlers
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode

METHODS = {
    "create": ("create", ["hypothesis", "commit"]),
    "relation_add": ("add_relation", ["relation", "portfolio", "commit"]),
    "relation_remove": ("end_relation", ["relation", "portfolio", "commit"]),
    "assumption_add": ("add_assumption", ["assumption", "hypothesis", "commit"]),
    "prediction_bind": ("bind_prediction", ["prediction", "hypothesis", "commit"]),
    "portfolio_compose": ("compose_portfolio", ["portfolio", "commit"]),
    "portfolio_revalidate": ("revalidate_portfolio", ["portfolio", "commit"]),
    "test_bind": ("bind_test", ["binding"]),
    "appraise": ("appraise", ["appraisal", "hypothesis", "commit"]),
    "appraise_quality": ("revise", ["hypothesis", "commit"]),
}
PREFIX = {
    "create": [],
    "relation_add": ["root:portfolio_expected"],
    "relation_remove": ["store:read_relation", "root:portfolio_expected"],
    "assumption_add": ["root:read_expected"],
    "prediction_bind": ["store:read_hypothesis"],
    "portfolio_compose": [],
    "portfolio_revalidate": ["root:portfolio"],
    "test_bind": ["store:read_prediction"],
    "appraise": ["root:read"],
    "appraise_quality": ["root:read"],
}


class Trace:
    def __init__(self, failure: str | None = None) -> None:
        self.events: list[str] = []
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.failure, self.error = failure, ValueError("original sentinel")

    def tick(self, label: str) -> None:
        self.events.append(label)
        if self.failure == label:
            raise self.error


class Row:
    def __init__(self, trace: Trace, label: str) -> None:
        self.trace, self.label = trace, label
        self.fields: dict[str, Any] = {
            "project_id": "p",
            "portfolio_id": "pf",
            "primary_intent": "EXPLORATORY",
            "gateway_results": {"intent": "PASS"},
            "invalidated_refs": ("test:b", "prediction:a"),
            "quality_profile": {"original": "MIXED"},
            "counterevidence_queries": (),
            "prediction_refs": (),
            "test_refs": (),
            "quality_gaps": ("gap:b", "gap:a"),
            "discrimination_matrix": ({"id": "b"}, {"id": "a"}),
            "coverage_state": "PARTIAL",
            "diversity_state": "MIXED",
            "discrimination_state": "UNKNOWN",
            "abstention_state": "AVAILABLE",
            "appraisal_mutation_allowed": False,
        }

    def __getattr__(self, name: str) -> Any:
        return self.fields[name]

    def model_dump(self, *, mode: str) -> dict[str, str]:
        assert mode == "json"
        self.trace.tick("dump:" + self.label)
        return {"id": self.label}


class Service:
    def __init__(self, trace: Trace, results: dict[str, Any]) -> None:
        self.trace, self.results = trace, results

    def __getattr__(self, name: str) -> Any:
        assert name in self.results, name

        def call(*args: Any, **kwargs: Any) -> Any:
            self.trace.calls.append((name, args, kwargs))
            self.trace.tick("service:" + name)
            return self.results[name]

        return call


class Store:
    def __init__(self, trace: Trace, rows: dict[str, Row]) -> None:
        self.trace, self.rows = trace, rows

    def __getattr__(self, name: str) -> Any:
        field = {
            "read_relation": "relation",
            "read_prediction": "prediction",
            "read_hypothesis": "current",
        }[name]

        def read(*args: Any) -> Row:
            assert args
            self.trace.tick("store:" + name)
            return self.rows[field]

        return read


def fixture(
    monkeypatch: pytest.MonkeyPatch, method: str, failure: str | None = None
) -> tuple[Any, dict[str, Any], Trace, dict[str, Row]]:
    trace = Trace(failure)
    rows = {
        name: Row(trace, name)
        for name in (
            "current",
            "hypothesis",
            "portfolio",
            "relation",
            "assumption",
            "prediction",
            "binding",
            "appraisal",
            "commit",
        )
    }
    h, p, c = rows["hypothesis"], rows["portfolio"], rows["commit"]
    results = {
        "create": (h, ("duplicate:b", "duplicate:a"), c),
        "add_relation": (rows["relation"], p, c),
        "end_relation": (rows["relation"], p, c),
        "add_assumption": (rows["assumption"], h, c),
        "bind_prediction": (rows["prediction"], h, c),
        "compose_portfolio": (p, c),
        "revalidate_portfolio": (p, c),
        "bind_test": rows["binding"],
        "appraise": (rows["appraisal"], h, c),
        "revise": (h, c),
    }
    handler = object.__new__(HypothesisHandlers)
    monkeypatch.setattr(handler, "_store", Store(trace, rows), raising=False)
    monkeypatch.setattr(handler, "_service", Service(trace, results), raising=False)

    def root_read(name: str, record: Row) -> Any:
        def read(*args: Any) -> Row:
            assert args
            trace.tick("root:" + name)
            return record

        return read

    for name in ("read", "read_expected", "portfolio", "portfolio_expected"):
        monkeypatch.setattr(
            handler,
            "_" + name,
            root_read(name, p if name.startswith("portfolio") else rows["current"]),
        )
    payloads: dict[str, dict[str, Any]] = {
        "create": {
            "object_id": "o",
            "portfolio_id": "pf",
            "statement": "s",
            "primary_intent": "EXPLORATORY",
            "evidence_basis": "source",
            "scope": {},
            "evidence_refs": ["e"],
        },
        "relation_add": {
            "portfolio_id": "pf",
            "source_hypothesis_id": "h1",
            "target_hypothesis_id": "h2",
            "relation_type": "SUPPORTS",
            "expected_portfolio_revision": "a" * 64,
            "evidence_refs": ["e"],
        },
        "relation_remove": {
            "relation_id": "r",
            "reason": "reason",
            "expected_portfolio_revision": "a" * 64,
            "evidence_refs": ["e"],
        },
        "assumption_add": {
            "hypothesis_id": "h",
            "expected_revision_digest": "a" * 64,
            "statement": "s",
            "role": "BOUNDARY",
            "evidence_refs": ["e"],
        },
        "prediction_bind": {
            "hypothesis_id": "h",
            "hypothesis_revision_digest": "a" * 64,
            "knowledge_cutoff": "2026-10-14T00:00:00Z",
            "prespecification_state": "POST_HOC",
            "conditions": {},
            "measurement_contract_ref": "contract:1",
            "assumption_refs": [],
            "expected_outcome": {},
            "discrimination_map": {},
        },
        "portfolio_compose": {
            "object_id": "o",
            "portfolio_id": "pf",
            "hypothesis_ids": ["h1", "h2"],
            "unknown_reserve": {},
        },
        "portfolio_revalidate": {"portfolio_id": "pf", "trigger_reason": "reason"},
        "test_bind": {
            "prediction_id": "prediction",
            "execution_ref": "execution",
            "observation_refs": ["e"],
            "test_validity_assessment_ref": "assessment",
        },
        "appraise": {
            "hypothesis_id": "h",
            "evidence_refs": ["e"],
            "test_assessment_refs": [],
            "appraisal_scope": {},
        },
        "appraise_quality": {"hypothesis_id": "h"},
        "intent_update": {
            "hypothesis_id": "h",
            "expected_revision_digest": "a" * 64,
            "primary_intent": "PREDICTIVE",
            "secondary_intents": [],
            "intent_profile_refs": ["profile:1"],
            "evidence_refs": ["e"],
            "reason": "reason",
        },
    }
    return handler, {"project_id": "p", **payloads[method]}, trace, rows


def expected_result(method: str) -> dict[str, Any]:
    h, p, c, r = {"id": "hypothesis"}, {"id": "portfolio"}, {"id": "commit"}, {"id": "relation"}
    return {
        "create": {
            "hypothesis": h,
            "gateway_result": {"intent": "PASS"},
            "duplicate_candidates": ["duplicate:b", "duplicate:a"],
            "commit": c,
        },
        "relation_add": {
            "relation": r,
            "portfolio": p,
            "quality_impact": "REVALIDATION_REQUIRED",
            "discrimination_impact": "REVALIDATION_REQUIRED",
            "commit": c,
        },
        "relation_remove": {"relation": r, "portfolio": p, "commit": c},
        "assumption_add": {
            "assumption": {"id": "assumption"},
            "hypothesis": h,
            "prediction_test_impact": ["test:b", "prediction:a"],
            "commit": c,
        },
        "prediction_bind": {
            "prediction": {"id": "prediction"},
            "hypothesis": h,
            "leakage_check": "PASS",
            "commit": c,
        },
        "portfolio_compose": {
            "portfolio": p,
            "duplicate_candidates": [],
            "quality_gaps": ["gap:b", "gap:a"],
            "discrimination_matrix_candidate": [{"id": "b"}, {"id": "a"}],
            "commit": c,
        },
        "portfolio_revalidate": {
            "portfolio": p,
            "coverage": "PARTIAL",
            "diversity": "MIXED",
            "discrimination": "UNKNOWN",
            "unknown_abstention": "AVAILABLE",
            "invalidations": ["gap:b", "gap:a"],
            "commit": c,
        },
        "test_bind": {
            "test_binding": {"id": "binding"},
            "appraisal_update_allowed": False,
            "affected_portfolio": None,
        },
        "appraise": {"appraisal": {"id": "appraisal"}, "hypothesis": h, "commit": c},
        "appraise_quality": {
            "hypothesis": h,
            "epistemic_profile": {
                "original": "MIXED",
                "counterevidence_readiness": "WEAK",
                "prediction_specificity": "WEAK",
                "testability": "MIXED",
                "discrimination": "NOT_ASSESSED",
            },
            "commit": c,
        },
    }[method]


@pytest.mark.asyncio
@pytest.mark.parametrize("method", tuple(METHODS))
async def test_response_values_order_and_identity(
    monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    handler, payload, trace, rows = fixture(monkeypatch, method)
    before = deepcopy({name: row.fields for name, row in rows.items()})
    result = await getattr(handler, method)(payload)
    service, dumps = METHODS[method]
    assert trace.events == [
        *PREFIX[method],
        "service:" + service,
        *["dump:" + name for name in dumps],
    ]
    expected = expected_result(method)
    assert result == expected and list(result) == list(expected)
    assert {name: row.fields for name, row in rows.items()} == before
    if method == "create":
        assert result["gateway_result"] is rows["hypothesis"].fields["gateway_results"]
    if method == "appraise_quality":
        quality = trace.calls[0][2]["updates"]["quality_profile"]
        assert result["epistemic_profile"] is quality
        assert quality is not rows["current"].fields["quality_profile"]
    if method == "portfolio_compose":
        assert (
            result["discrimination_matrix_candidate"][0]
            is rows["portfolio"].fields["discrimination_matrix"][0]
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,failed_dump", [(m, d) for m, (_, dumps) in METHODS.items() for d in dumps]
)
async def test_response_dump_value_error_stays_outside_domain_conversion(
    monkeypatch: pytest.MonkeyPatch, method: str, failed_dump: str
) -> None:
    handler, payload, trace, _ = fixture(monkeypatch, method, "dump:" + failed_dump)
    with pytest.raises(ValueError) as caught:
        await getattr(handler, method)(payload)
    assert caught.value is trace.error
    service, dumps = METHODS[method]
    assert trace.events == [
        *PREFIX[method],
        "service:" + service,
        *["dump:" + name for name in dumps[: dumps.index(failed_dump) + 1]],
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("method", tuple(METHODS))
async def test_response_service_value_error_keeps_domain_conversion(
    monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    service = METHODS[method][0]
    handler, payload, trace, _ = fixture(monkeypatch, method, "service:" + service)
    with pytest.raises(RpcApplicationError) as caught:
        await getattr(handler, method)(payload)
    assert caught.value.code == RpcErrorCode.DOMAIN_REJECTED
    assert caught.value.__cause__ is trace.error
    assert trace.events == [*PREFIX[method], "service:" + service]


@pytest.mark.asyncio
@pytest.mark.parametrize("counter,prediction,test", tuple(product((False, True), repeat=3)))
async def test_payload_quality_flags_copy_and_same_object(
    monkeypatch: pytest.MonkeyPatch, counter: bool, prediction: bool, test: bool
) -> None:
    handler, payload, trace, rows = fixture(monkeypatch, "appraise_quality")
    fields = rows["current"].fields
    fields.update(
        counterevidence_queries=("q",) if counter else (),
        prediction_refs=("pred",) if prediction else (),
        test_refs=("test",) if test else (),
    )
    original = deepcopy(fields)
    result = await handler.appraise_quality(payload)
    updates = trace.calls[0][2]
    quality = updates["updates"]["quality_profile"]
    assert quality == {
        "original": "MIXED",
        "counterevidence_readiness": "STRONG" if counter else "WEAK",
        "prediction_specificity": "STRONG" if prediction else "WEAK",
        "testability": "STRONG" if test else "MIXED",
        "discrimination": "MIXED" if prediction else "NOT_ASSESSED",
    }
    assert result["epistemic_profile"] is quality and quality is not fields["quality_profile"]
    assert updates["event_type"] == "hypothesis/qualityUpdated"
    assert updates["invalidate_derived"] is False and updates["evidence_refs"] == ()
    assert fields == original


class Intents:
    def __init__(self, trace: Trace, member: bool) -> None:
        self.trace, self.member = trace, member

    def __contains__(self, value: object) -> bool:
        assert value == "PREDICTIVE"
        self.trace.tick("causal_membership")
        return self.member


@pytest.mark.asyncio
@pytest.mark.parametrize("member", [False, True])
async def test_payload_intent_uses_root_policy_and_preserves_revision_lookup(
    monkeypatch: pytest.MonkeyPatch, member: bool
) -> None:
    handler, payload, trace, rows = fixture(monkeypatch, "intent_update")
    policy = Intents(trace, member)
    monkeypatch.setattr(module, "CAUSAL_INTENTS", policy)
    rows["hypothesis"].fields["primary_intent"] = "PREDICTIVE"
    result: dict[str, Any] = {"marker": "patched"}

    def project(before: Any, after: Any, commit: Any) -> dict[str, Any]:
        assert (
            before is rows["current"] and after is rows["hypothesis"] and commit is rows["commit"]
        )
        trace.tick("revision_result")
        return result

    monkeypatch.setattr(HypothesisHandlers, "_revision_result", staticmethod(project))
    actual = await handler.intent_update(payload)
    assert actual is result and actual["gateway_profile_delta"] == {
        "before": "EXPLORATORY",
        "after": "PREDICTIVE",
    }
    assert trace.events == [
        "root:read_expected",
        "causal_membership",
        "service:revise",
        "revision_result",
    ]
    kwargs = trace.calls[0][2]
    assert kwargs["updates"] == {
        "primary_intent": "PREDICTIVE",
        "secondary_intents": (),
        "intent_profile_refs": ("profile:1",),
        "causal_profile": {"applicability": "REQUIRED", "primary_locus": "UNCLASSIFIED"}
        if member
        else {"applicability": "NOT_APPLICABLE"},
    }
    assert (
        kwargs["event_type"] == "hypothesis/intentChanged" and kwargs["invalidate_derived"] is True
    )
    assert kwargs["evidence_refs"] == ("e",)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value", [("primary_intent", "UNSUPPORTED"), ("secondary_intents", ["UNSUPPORTED"])]
)
async def test_payload_intent_guard_precedes_policy_and_revision(
    monkeypatch: pytest.MonkeyPatch, field: str, value: Any
) -> None:
    handler, payload, trace, _ = fixture(monkeypatch, "intent_update")
    payload[field] = value
    monkeypatch.setattr(module, "CAUSAL_INTENTS", Intents(trace, False))
    with pytest.raises(RpcApplicationError) as caught:
        await handler.intent_update(payload)
    assert caught.value.code == RpcErrorCode.DOMAIN_REJECTED
    assert trace.events == ["root:read_expected"] and trace.calls == []
