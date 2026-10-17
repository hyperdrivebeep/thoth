"""Characterize the original root's draft and dependency evaluation before extraction."""

from __future__ import annotations

from collections.abc import Generator, Iterator
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import pytest

from thoth.application.services import hypothesis_service as module
from thoth.application.services.hypothesis_service import HypothesisService
from thoth.domain.hypothesis_full import HypothesisRecord

NOW = datetime(2026, 9, 4, 12, tzinfo=UTC)
CREATE_KEYS = (
    "hypothesis_revision_id",
    "hypothesis_id",
    "project_id",
    "object_id",
    "portfolio_id",
    "statement",
    "observed_problem",
    "primary_intent",
    "secondary_intents",
    "intent_profile_refs",
    "evidence_basis",
    "scope",
    "evidence_refs",
    "causal_profile",
    "development_stage",
    "empirical_appraisal",
    "freshness",
    "prespecification_state",
    "quality_profile",
    "gateway_results",
    "created_at",
)


class Text(str):
    events: list[str]
    label: str

    def __new__(cls, value: str, events: list[str], label: str) -> Text:
        result = super().__new__(cls, value)
        result.events, result.label = events, label
        return result

    def strip(self, chars: str | None = None) -> str:
        self.events.append(self.label + ".strip")
        return super().strip(chars)

    def lower(self) -> str:
        self.events.append(self.label + ".lower")
        return super().lower()


class Scope(dict[str, str]):
    def __init__(self, values: dict[str, str], events: list[str]):
        super().__init__(values)
        self.events = events

    def __bool__(self) -> bool:
        self.events.append("scope.bool")
        return len(self) > 0


class Axes:
    def __init__(self, values: tuple[str, ...], events: list[str]):
        self.values, self.events = values, events

    def __iter__(self) -> Iterator[str]:
        self.events.append("quality.axes")
        return iter(self.values)


class Causal:
    def __init__(self, values: set[str], events: list[str]):
        self.values, self.events = values, events

    def __contains__(self, value: object) -> bool:
        self.events.append("causal.contains")
        return value in self.values


class Clock:
    def __init__(self, events: list[str], fault: str | None, error: RuntimeError):
        self.events, self.fault, self.error = events, fault, error

    def now(self) -> datetime:
        self.events.append("clock")
        if self.fault == "clock":
            raise self.error
        return NOW


class IDs:
    def __init__(self, events: list[str], fault: str | None, error: RuntimeError):
        self.events, self.fault, self.error = events, fault, error

    def new(self, prefix: str) -> str:
        self.events.append("id:" + prefix)
        if self.fault == prefix:
            raise self.error
        return prefix + ":recorded"


class Ledger:
    def __init__(self, events: list[str]):
        self.events = events

    @contextmanager
    def transaction(self) -> Generator[None]:
        self.events.append("tx.enter")
        try:
            yield
        finally:
            self.events.append("tx.exit")


class Store:
    def __init__(self, events: list[str]):
        self.events = events

    def list_hypotheses(self, _project: str) -> tuple[HypothesisRecord, ...]:
        self.events.append("duplicates")
        return ()


class Object:
    def __init__(self, events: list[str]):
        self.events = events

    @property
    def problem_frame(self) -> str:
        self.events.append("object.frame")
        return "Explain clock drift"


def harness(
    monkeypatch: pytest.MonkeyPatch, fault: str | None = None
) -> tuple[HypothesisService, list[str], dict[str, Any], RuntimeError]:
    events: list[str] = []
    captured: dict[str, Any] = {}
    error = RuntimeError("recorded dependency failure")
    service = object.__new__(HypothesisService)
    vars(service).update(
        _ledger=Ledger(events),
        _store=Store(events),
        _clock=Clock(events, fault, error),
        _ids=IDs(events, fault, error),
    )
    original_digest = vars(HypothesisService)["_digest"].__func__

    def head(*_args: Any) -> Object:
        events.append("head")
        return Object(events)

    def intents(*_args: Any) -> None:
        events.append("intents")

    def evidence(*_args: Any) -> None:
        events.append("evidence")

    def digest(draft: dict[str, object]) -> str:
        events.append("digest")
        captured["draft"] = draft
        return original_digest(draft)

    def persist(
        record: HypothesisRecord, event_type: str, **kwargs: Any
    ) -> tuple[HypothesisRecord, Any]:
        events.append("persist")
        captured.update(record=record, event_type=event_type, persist_options=kwargs)
        return record, None

    monkeypatch.setattr(service, "_check_object_head", head)
    monkeypatch.setattr(service, "_validate_intents", intents)
    monkeypatch.setattr(service, "_validate_evidence", evidence)
    monkeypatch.setattr(service, "_digest", digest)
    monkeypatch.setattr(service, "_persist_hypothesis", persist)
    monkeypatch.setattr(module, "QUALITY_AXES", Axes(module.QUALITY_AXES, events))
    monkeypatch.setattr(module, "CAUSAL_INTENTS", Causal(module.CAUSAL_INTENTS, events))
    return service, events, captured, error


def create_arguments(
    events: list[str],
    *,
    has_evidence: bool = True,
    has_scope: bool = True,
    statement: str = "Clock drift",
    intent: str = "DIAGNOSTIC_CAUSAL",
) -> dict[str, Any]:
    return {
        "project_id": "p",
        "object_id": "o",
        "portfolio_id": "portfolio",
        "statement": Text(statement, events, "statement"),
        "primary_intent": Text(intent, events, "intent"),
        "secondary_intents": (),
        "evidence_basis": Text("SOURCE", events, "basis"),
        "scope": Scope({"workstream": "integration"} if has_scope else {}, events),
        "evidence_refs": ("span:1",) if has_evidence else (),
        "prespecification_state": "UNKNOWN",
    }


@pytest.mark.parametrize(
    "has_evidence,has_scope,statement,intent",
    [
        (True, True, "Clock drift", "DIAGNOSTIC_CAUSAL"),
        (True, True, "Clock drift", "PREDICTIVE"),
        (False, True, "Clock drift", "EXPLORATORY"),
        (True, False, "Clock drift", "DIAGNOSTIC_CAUSAL"),
        (False, False, "   ", "EXPLANATORY_MECHANISTIC"),
    ],
)
def test_create_records_order_identity_policy_and_partial_state(
    monkeypatch: pytest.MonkeyPatch,
    has_evidence: bool,
    has_scope: bool,
    statement: str,
    intent: str,
) -> None:
    expected_axes = module.QUALITY_AXES
    service, events, captured, _error = harness(monkeypatch)
    args = create_arguments(
        events, has_evidence=has_evidence, has_scope=has_scope, statement=statement, intent=intent
    )
    scope_before = dict(args["scope"])
    record, duplicates, _commit = service.create(**args)
    draft = captured["draft"]
    assert tuple(draft) == CREATE_KEYS
    assert draft["scope"] is args["scope"] and draft["evidence_refs"] is args["evidence_refs"]
    assert (
        draft["secondary_intents"] is args["secondary_intents"]
        and dict(args["scope"]) == scope_before
    )
    assert [e for e in events if e.startswith("id:") or e == "clock"] == [
        "id:hypothesis-revision",
        "id:hypothesis",
        "clock",
    ]
    assert (
        events.index("statement.strip")
        < events.index("quality.axes")
        < events.index("id:hypothesis-revision")
    )
    assert (
        events.index("id:hypothesis")
        < events.index("object.frame")
        < events.index("intent.lower")
        < events.index("causal.contains")
        < events.index("clock")
        < events.index("digest")
        < events.index("persist")
    )
    assert (
        events[:5] == ["tx.enter", "head", "intents", "evidence", "duplicates"]
        and events[-1] == "tx.exit"
    )
    assert record.development_stage == (
        "GROUNDED_CANDIDATE" if has_evidence and has_scope else "DRAFT"
    )
    assert record.gateway_results["atomic_statement"] == ("PASS" if statement.strip() else "FAIL")
    assert record.causal_profile["applicability"] == (
        "REQUIRED"
        if intent in {"DIAGNOSTIC_CAUSAL", "EXPLANATORY_MECHANISTIC"}
        else "NOT_APPLICABLE"
    )
    assert tuple(draft["quality_profile"]) == expected_axes
    assert record.created_at == NOW and duplicates == ()


@pytest.mark.parametrize("fault", ["hypothesis-revision", "hypothesis", "clock"])
def test_create_dependency_failure_identity_and_evaluation_boundary(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    service, events, captured, error = harness(monkeypatch, fault)
    with pytest.raises(RuntimeError) as raised:
        service.create(**create_arguments(events))
    assert raised.value is error and captured == {}
    assert events.index("quality.axes") < events.index("id:hypothesis-revision")
    ports = [e for e in events if e.startswith("id:") or e == "clock"]
    assert (
        ports
        == ["id:hypothesis-revision", "id:hypothesis", "clock"][
            : {"hypothesis-revision": 1, "hypothesis": 2, "clock": 3}[fault]
        ]
    )
    assert events[-1] == "tx.exit" and "digest" not in events and "persist" not in events


def current_record() -> HypothesisRecord:
    return HypothesisRecord(
        hypothesis_revision_id="hrev:old",
        hypothesis_id="h:1",
        project_id="p",
        object_id="o",
        portfolio_id="portfolio",
        statement="Clock drift",
        observed_problem="An observed drift",
        primary_intent="DIAGNOSTIC_CAUSAL",
        evidence_basis="SOURCE",
        scope={"workstream": "integration"},
        evidence_refs=("span:1",),
        prediction_refs=("prediction:1", "prediction:2"),
        test_refs=("test:1",),
        invalidated_refs=("prediction:1", "old:flag"),
        empirical_appraisal="EVIDENCE_FAVORS",
        revision_digest="a" * 64,
        receipt_ref="receipt:old",
        created_at=NOW,
    )


@pytest.mark.parametrize("invalidate", [False, True])
@pytest.mark.parametrize("intent", ["DIAGNOSTIC_CAUSAL", None])
def test_revise_preserves_dump_identity_key_order_and_inputs(
    monkeypatch: pytest.MonkeyPatch, invalidate: bool, intent: str | None
) -> None:
    service, events, captured, _error = harness(monkeypatch)
    current = current_record()
    original_dump = HypothesisRecord.model_dump
    original_value = original_dump(current, mode="python")
    dumped: list[dict[str, Any]] = []

    def model_dump(record: HypothesisRecord, *args: Any, **kwargs: Any) -> dict[str, Any]:
        result = original_dump(record, *args, **kwargs)
        if record is current:
            events.append("model_dump")
            dumped.append(result)
        return result

    monkeypatch.setattr(HypothesisRecord, "model_dump", model_dump)
    new_scope = {"workstream": "new"}
    updates: dict[str, object] = {
        "statement": "Revised clock drift",
        "scope": new_scope,
        "primary_intent": intent,
    }
    before_updates = deepcopy(updates)
    revised, _commit = service.revise(
        current,
        updates=updates,
        event_type="hypothesis/updated",
        evidence_refs=("span:1",),
        invalidate_derived=invalidate,
    )
    draft = captured["draft"]
    assert len(dumped) == 1 and draft is dumped[0] and draft["scope"] is new_scope
    assert tuple(draft) == tuple(
        k for k in original_value if k not in {"revision_digest", "receipt_ref"}
    )
    assert updates == before_updates and original_dump(current, mode="python") == original_value
    assert events == [
        "evidence",
        "model_dump",
        "id:hypothesis-revision",
        "clock",
        "digest",
        "persist",
    ]
    assert (
        revised.supersedes_revision_digest == current.revision_digest
        and revised.receipt_ref is None
    )
    if invalidate:
        assert revised.prediction_refs == revised.test_refs == ()
        assert revised.invalidated_refs == (
            "prediction:1",
            "old:flag",
            "prediction:2",
            "test:1",
            "APPRAISALS",
        )
        assert revised.development_stage == (
            "GROUNDED_CANDIDATE" if intent is not None else "DRAFT"
        )
        assert revised.empirical_appraisal == "UNASSESSED"
    else:
        assert (
            revised.prediction_refs == current.prediction_refs
            and revised.test_refs == current.test_refs
        )
        assert revised.empirical_appraisal == current.empirical_appraisal


@pytest.mark.parametrize("fault", ["hypothesis-revision", "clock"])
def test_revise_dependency_failure_keeps_original_input(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    service, events, captured, error = harness(monkeypatch, fault)
    current = current_record()
    before = current.model_dump(mode="python")
    updates: dict[str, object] = {"statement": "Revised"}
    with pytest.raises(RuntimeError) as raised:
        service.revise(
            current, updates=updates, event_type="hypothesis/updated", invalidate_derived=True
        )
    assert raised.value is error and captured == {}
    assert current.model_dump(mode="python") == before and updates == {"statement": "Revised"}
    assert events == ["evidence", "id:hypothesis-revision"] + (
        ["clock"] if fault == "clock" else []
    )
