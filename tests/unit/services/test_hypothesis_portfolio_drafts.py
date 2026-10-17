"""Internal portfolio construction contracts; public RPC still requires two hypotheses."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from tests.unit.services.test_hypothesis_record_drafts import NOW, Clock, IDs, Ledger

from thoth.application.services.hypothesis_service import HypothesisService
from thoth.domain.hypothesis_full import HypothesisPortfolioRecord

KEYS = (
    "portfolio_revision_id",
    "portfolio_id",
    "project_id",
    "object_id",
    "hypothesis_refs",
    "relation_refs",
    "unknown_reserve",
    "coverage_state",
    "diversity_state",
    "discrimination_state",
    "abstention_state",
    "quality_gaps",
    "discrimination_matrix",
    "supersedes_revision_digest",
    "created_at",
)


class Statement(str):
    events: list[str]
    error: RuntimeError | None

    def __new__(cls, text: str, events: list[str], error: RuntimeError | None) -> Statement:
        value = super().__new__(cls, text)
        value.events, value.error = events, error
        return value

    def casefold(self) -> str:
        self.events.append("casefold:" + str(self))
        if self.error is not None:
            raise self.error
        return super().casefold()


class Refs(tuple[str, ...]):
    events: list[str]
    label: str

    def __new__(cls, refs: tuple[str, ...], events: list[str], label: str) -> Refs:
        value = super().__new__(cls, refs)
        value.events, value.label = events, label
        return value

    def __bool__(self) -> bool:
        self.events.append(self.label)
        return len(self) > 0


class Hypothesis:
    def __init__(
        self,
        index: int,
        text: str,
        counter: bool,
        prediction: bool,
        events: list[str],
        error: RuntimeError | None,
    ):
        self.hypothesis_id = "h:" + str(index)
        self.object_id = "o"
        self.statement = Statement(text, events, error)
        self.counterevidence_queries = Refs(
            ("query",) if counter else (), events, f"counter:{index}"
        )
        self.prediction_refs = Refs(
            ("prediction",) if prediction else (), events, f"prediction:{index}"
        )


class Store:
    def __init__(
        self,
        events: list[str],
        current: HypothesisPortfolioRecord | None,
        fault: str | None,
        error: RuntimeError,
    ):
        self.events, self.current, self.fault, self.error = events, current, fault, error

    def read_portfolio(
        self, project: str, identifier: str, revision: str | None
    ) -> HypothesisPortfolioRecord | None:
        self.events.append("read:" + identifier)
        assert project == "p" and revision is None
        if self.fault == "read":
            raise self.error
        return self.current


def portfolio(identifier: str = "portfolio") -> HypothesisPortfolioRecord:
    return HypothesisPortfolioRecord(
        portfolio_revision_id="pr:old",
        portfolio_id=identifier,
        project_id="p",
        object_id="o",
        hypothesis_refs=("h:1", "h:2"),
        relation_refs=("relation:old",),
        unknown_reserve={"reason": "unobserved"},
        quality_gaps=("old gap",),
        revision_digest="a" * 64,
        receipt_ref="receipt:old",
        created_at=NOW,
    )


def harness(
    monkeypatch: pytest.MonkeyPatch,
    *,
    count: int = 2,
    duplicate: bool = False,
    counter: bool = True,
    prediction: bool = True,
    current: HypothesisPortfolioRecord | None = None,
    fault: str | None = None,
) -> tuple[HypothesisService, list[str], dict[str, Any], tuple[Hypothesis, ...], RuntimeError]:
    events: list[str] = []
    captured: dict[str, Any] = {}
    error = RuntimeError("portfolio dependency failure")
    hypotheses = tuple(
        Hypothesis(
            i + 1,
            "CLOCK" if duplicate else f"Clock {i}",
            counter,
            prediction,
            events,
            error if fault == "casefold" else None,
        )
        for i in range(count)
    )
    service = object.__new__(HypothesisService)
    vars(service).update(
        _ledger=Ledger(events),
        _store=Store(events, current, fault, error),
        _clock=Clock(events, fault, error),
        _ids=IDs(events, fault, error),
    )
    original = vars(HypothesisService)["_portfolio_digest"].__func__

    def head(*_args: Any) -> None:
        events.append("head")

    def read(*_args: Any) -> Any:
        events.append("hypotheses")
        return hypotheses

    def digest(draft: dict[str, object]) -> str:
        events.append("digest")
        captured["draft"] = draft
        return original(draft)

    def persist(
        record: HypothesisPortfolioRecord, event_type: str
    ) -> tuple[HypothesisPortfolioRecord, Any]:
        events.append("persist")
        captured.update(record=record, event_type=event_type)
        return record, None

    monkeypatch.setattr(service, "_check_object_head", head)
    monkeypatch.setattr(service, "_hypotheses", read)
    monkeypatch.setattr(service, "_portfolio_digest", digest)
    monkeypatch.setattr(service, "_persist_portfolio", persist)
    return service, events, captured, hypotheses, error


@pytest.mark.parametrize(
    "count,identifier,duplicate,counter,prediction,existing",
    [
        (0, None, False, False, False, False),
        (1, "portfolio", False, False, False, True),
        (2, None, False, True, True, False),
        (2, "", True, True, True, True),
        (2, "portfolio", False, True, False, False),
        (2, "portfolio", False, False, True, False),
        (2, "portfolio", True, False, False, True),
    ],
)
def test_internal_compose_order_quality_and_identity(
    monkeypatch: pytest.MonkeyPatch,
    count: int,
    identifier: str | None,
    duplicate: bool,
    counter: bool,
    prediction: bool,
    existing: bool,
) -> None:
    effective = identifier or "hypothesis-portfolio:recorded"
    old = portfolio(effective) if existing else None
    service, events, captured, hypotheses, _error = harness(
        monkeypatch,
        count=count,
        duplicate=duplicate,
        counter=counter,
        prediction=prediction,
        current=old,
    )
    reserve: dict[str, object] = {
        "alternatives_considered": ["unobserved"],
        "next_checks": ["measure"],
    }
    reserve_before = deepcopy(reserve)
    ids = tuple(h.hypothesis_id for h in hypotheses)
    record, _commit = service.compose_portfolio(
        project_id="p",
        object_id="o",
        portfolio_id=identifier,
        hypothesis_ids=ids,
        unknown_reserve=reserve,
    )
    draft = captured["draft"]
    assert (
        tuple(draft) == KEYS
        and draft["hypothesis_refs"] is ids
        and draft["unknown_reserve"] is reserve
    )
    assert reserve == reserve_before
    assert draft["relation_refs"] is (() if old is None else old.relation_refs)
    assert record.supersedes_revision_digest == (None if old is None else old.revision_digest)
    ports = [e for e in events if e.startswith("id:") or e.startswith("read:") or e == "clock"]
    assert ports == ([] if identifier else ["id:hypothesis-portfolio"]) + [
        "read:" + effective,
        "id:portfolio-revision",
        "clock",
    ]
    assert events[:3] == ["tx.enter", "head", "hypotheses"] and events[-3:] == [
        "digest",
        "persist",
        "tx.exit",
    ]
    if count:
        assert (
            events.index("read:" + effective)
            < next(i for i, e in enumerate(events) if e.startswith("casefold:"))
            < events.index("id:portfolio-revision")
        )
        assert (
            events.index("counter:1")
            < events.index("prediction:1")
            < events.index("id:portfolio-revision")
        )
        assert (
            events.index("id:portfolio-revision")
            < max(i for i, e in enumerate(events) if e.startswith("prediction:"))
            < events.index("clock")
        )
        assert events.count("counter:1") == 1 and events.count("prediction:1") == 2
        if count == 2:
            assert events.count("counter:2") == (1 if counter else 0)
            assert events.count("prediction:2") == (2 if prediction else 0)
    expected_gaps = tuple(
        g
        for g, missing in [
            ("semantic diversity", count < 2 or duplicate),
            ("counterevidence route", not count or not counter),
            ("prediction coverage", not count or not prediction),
        ]
        if missing
    )
    assert record.quality_gaps == expected_gaps
    assert record.coverage_state == (
        "NOT_ASSESSED" if count == 0 else "PARTIAL" if expected_gaps else "COVERED"
    )
    assert record.diversity_state == (
        "NOT_ASSESSED" if count < 2 else "PARAPHRASE_RISK" if duplicate else "DIVERSE"
    )
    assert record.discrimination_state == (
        "NOT_ASSESSED" if count == 0 else "CANDIDATE" if prediction else "MISSING"
    )
    assert record.abstention_state == "AVAILABLE" and record.created_at == NOW


@pytest.mark.parametrize(
    "fault", ["hypothesis-portfolio", "read", "casefold", "portfolio-revision", "clock"]
)
def test_compose_dependency_failures_are_not_advanced(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    service, events, captured, _hypotheses, error = harness(monkeypatch, fault=fault)
    with pytest.raises(RuntimeError) as raised:
        service.compose_portfolio(
            project_id="p",
            object_id="o",
            portfolio_id=None,
            hypothesis_ids=("h:1", "h:2"),
            unknown_reserve={"reason": "unknown"},
        )
    assert raised.value is error and captured == {} and events[-1] == "tx.exit"
    assert "digest" not in events and "persist" not in events
    ports = [e for e in events if e.startswith("id:") or e.startswith("read:") or e == "clock"]
    all_ports = [
        "id:hypothesis-portfolio",
        "read:hypothesis-portfolio:recorded",
        "id:portfolio-revision",
        "clock",
    ]
    assert (
        ports
        == all_ports[
            : {
                "hypothesis-portfolio": 1,
                "read": 2,
                "casefold": 2,
                "portfolio-revision": 3,
                "clock": 4,
            }[fault]
        ]
    )
    if fault in {"hypothesis-portfolio", "read"}:
        assert not any(e.startswith("casefold:") for e in events)


@pytest.mark.parametrize("failure", ["object", "partial", "empty_reserve"])
def test_compose_root_guards_precede_ids_and_lookup(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    service, events, captured, hypotheses, _error = harness(
        monkeypatch, count=1 if failure == "partial" else 2
    )
    if failure == "object":
        hypotheses[0].object_id = "another-object"
    with pytest.raises(ValueError) as raised:
        service.compose_portfolio(
            project_id="p",
            object_id="o",
            portfolio_id=None,
            hypothesis_ids=tuple(h.hypothesis_id for h in hypotheses),
            unknown_reserve={},
        )
    assert (
        str(raised.value)
        == {
            "object": "portfolio cannot cross DecisionObject boundaries",
            "partial": "0/1 portfolio requires alternative review and next checks",
            "empty_reserve": "portfolio requires an explicit unknown reserve",
        }[failure]
    )
    assert events == ["tx.enter", "head", "hypotheses", "tx.exit"] and captured == {}


@pytest.mark.parametrize("fault", [None, "portfolio-revision", "clock"])
def test_revision_draft_preserves_inputs_identity_and_failure_order(
    monkeypatch: pytest.MonkeyPatch, fault: str | None
) -> None:
    service, events, captured, _hypotheses, error = harness(monkeypatch, fault=fault)
    current = portfolio()
    original_dump = HypothesisPortfolioRecord.model_dump
    original_value = original_dump(current, mode="python")
    dumps: list[dict[str, Any]] = []

    def dump(record: HypothesisPortfolioRecord, *args: Any, **kwargs: Any) -> dict[str, Any]:
        result = original_dump(record, *args, **kwargs)
        if record is current:
            events.append("dump")
            dumps.append(result)
        return result

    monkeypatch.setattr(HypothesisPortfolioRecord, "model_dump", dump)
    reserve: dict[str, object] = {"reason": "retained uncertainty"}
    updates: dict[str, object] = {"unknown_reserve": reserve, "quality_gaps": ("changed",)}
    unchanged_updates = deepcopy(updates)
    revise = vars(HypothesisService)["_revise_portfolio"]
    if fault:
        with pytest.raises(RuntimeError) as raised:
            revise(service, current, updates=updates, event_type="hypothesis/relationChanged")
        assert raised.value is error and captured == {}
        assert events == ["dump", "id:portfolio-revision"] + (["clock"] if fault == "clock" else [])
    else:
        record, _commit = revise(
            service, current, updates=updates, event_type="hypothesis/relationChanged"
        )
        assert captured["draft"] is dumps[0] and captured["draft"]["unknown_reserve"] is reserve
        assert tuple(captured["draft"]) == tuple(
            k for k in original_value if k not in {"revision_digest", "receipt_ref"}
        )
        assert events == ["dump", "id:portfolio-revision", "clock", "digest", "persist"]
        assert (
            record.supersedes_revision_digest == current.revision_digest
            and record.receipt_ref is None
        )
    assert len(dumps) == 1 and original_dump(current, mode="python") == original_value
    assert updates == unchanged_updates


def test_duplicate_hypothesis_refs_are_not_deduplicated(monkeypatch: pytest.MonkeyPatch) -> None:
    service, _events, captured, hypotheses, _error = harness(monkeypatch, duplicate=True)
    hypotheses[1].hypothesis_id = hypotheses[0].hypothesis_id
    ids = (hypotheses[0].hypothesis_id, hypotheses[0].hypothesis_id)
    record, _commit = service.compose_portfolio(
        project_id="p",
        object_id="o",
        portfolio_id="portfolio",
        hypothesis_ids=ids,
        unknown_reserve={"reason": "unknown"},
    )
    assert captured["draft"]["hypothesis_refs"] is ids
    assert record.hypothesis_refs == ids and record.diversity_state == "PARAPHRASE_RISK"
