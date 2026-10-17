"""Observe original auxiliary construction at the root digest boundary, without publication."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import Any

import pytest
from tests.unit.services.test_hypothesis_portfolio_drafts import portfolio
from tests.unit.services.test_hypothesis_record_drafts import NOW, Clock, IDs, current_record

from thoth.application.services.hypothesis_service import HypothesisService
from thoth.application.services.hypothesis_test_lifecycle import PreparedPredictionBasis
from thoth.domain.hypothesis_full import HypothesisRelationRecord, PredictionRecord
from thoth.domain.test_validity import ExpectedRange, ResearchMeasurementContract


class DraftCaptured(RuntimeError):
    pass


class Mapping(dict[str, Any]):
    def __init__(self, values: dict[str, Any], events: list[str]):
        super().__init__(values)
        self.events = events

    def get(self, key: str, default: Any = None) -> Any:
        self.events.append("get:" + key)
        return super().get(key, default)

    def items(self):
        self.events.append("scope.items")
        return super().items()


class Basis(PreparedPredictionBasis):
    def __getattribute__(self, name: str) -> Any:
        if name in {"contract", "contract_digest", "semantic_digest"}:
            object.__getattribute__(self, "observed_events").append("basis:" + name)
        return super().__getattribute__(name)


def make_basis(events: list[str]) -> Basis:
    contract = ResearchMeasurementContract(
        record_type="RESEARCH_MEASUREMENT_CONTRACT",
        contract_id="contract",
        method="ARITHMETIC_MEAN",
        procedure_version="numeric-mean:1",
        measure="latency",
        unit="ms",
        conditions={"dataset_version": "v1"},
        minimum_samples=2,
        maximum_samples=100,
        image_digest="scripted",
        runtime_version="scripted:1",
    )
    basis = Basis(
        contract,
        "b" * 64,
        "artifact:contract",
        ("span:1",),
        "a" * 64,
        "c" * 64,
        1,
        NOW,
        ExpectedRange(
            type="RANGE", measure="latency", unit="ms", lower=Decimal("9"), upper=Decimal("13")
        ),
    )
    object.__setattr__(basis, "observed_events", events)
    return basis


def relation(active: bool = True) -> HypothesisRelationRecord:
    return HypothesisRelationRecord(
        relation_revision_id="rr:old",
        relation_id="r:1",
        project_id="p",
        portfolio_id="portfolio",
        source_hypothesis_id="h:1",
        relation_type="ALTERNATIVE_TO",
        target_hypothesis_id="h:2",
        evidence_refs=("span:1",),
        active=active,
        revision_digest="a" * 64,
        created_at=NOW,
    )


class Store:
    def __init__(self, events: list[str]):
        self.events = events

    def list_test_bindings(self, *_args: Any) -> tuple[()]:
        self.events.append("bindings")
        return ()


def harness(
    monkeypatch: pytest.MonkeyPatch, fault: str | None = None
) -> tuple[HypothesisService, list[str], dict[str, Any], RuntimeError, DraftCaptured]:
    events: list[str] = []
    captured: dict[str, Any] = {}
    error, stop = RuntimeError("dependency failure"), DraftCaptured("root digest boundary")
    service = object.__new__(HypothesisService)
    vars(service).update(
        _ids=IDs(events, fault, error),
        _clock=Clock(events, fault, error),
        _store=Store(events),
        _test_lifecycle=object(),
    )

    def evidence(*_args: Any) -> None:
        events.append("evidence")

    def hypotheses(*_args: Any) -> tuple[()]:
        events.append("hypotheses")
        return ()

    def digest(kind: str, draft: dict[str, object]) -> str:
        events.append("digest:" + kind)
        captured.update(kind=kind, draft=draft)
        raise stop

    monkeypatch.setattr(service, "_validate_evidence", evidence)
    monkeypatch.setattr(service, "_hypotheses", hypotheses)
    monkeypatch.setattr(service, "_aux_digest", digest)

    def relation_digest(draft: dict[str, object]) -> str:
        return digest("RELATION", draft)

    monkeypatch.setattr(service, "_relation_digest", relation_digest)
    original = PredictionRecord.model_validate

    def validate(
        _cls: type[PredictionRecord], data: Any, *args: Any, **kwargs: Any
    ) -> PredictionRecord:
        events.append("prediction.validate")
        captured["prediction_draft"] = data
        return original(data, *args, **kwargs)

    monkeypatch.setattr(PredictionRecord, "model_validate", classmethod(validate))
    return service, events, captured, error, stop


def invoke(
    service: Any,
    kind: str,
    events: list[str],
    *,
    validation_route: str | None = None,
    inputs_out: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current = current_record()
    refs = ("span:1",)
    inputs: dict[str, Any] = inputs_out if inputs_out is not None else {}
    inputs.update(
        {
            "current": current,
            "refs": refs,
            "before": current.model_dump(mode="python"),
        }
    )
    if kind == "relation":
        service.add_relation(
            portfolio(),
            source_hypothesis_id="h:1",
            target_hypothesis_id="h:2",
            relation_type="ALTERNATIVE_TO",
            evidence_refs=refs,
            semantic_role=None,
        )
    elif kind == "end_relation":
        old = relation()
        inputs["relation"] = old
        inputs["relation_before"] = old.model_dump(mode="python")
        service.end_relation(portfolio(), old, reason="end", evidence_refs=refs)
    elif kind == "assumption":
        service.add_assumption(
            current,
            statement="Need calibration",
            role="MEASUREMENT",
            evidence_refs=refs,
            validation_route=validation_route,
        )
    elif kind == "prediction":
        basis = make_basis(events)
        conditions = Mapping(
            {"dataset_version": "v1", "population": "scope", "time_window": "window"}, events
        )
        expected: dict[str, object] = {
            "type": "RANGE",
            "measure": "latency",
            "unit": "ms",
            "lower": 9,
            "upper": 13,
        }
        discrimination: dict[str, object] = {"comparison_hypotheses": ("h:2",)}
        inputs.update(
            basis=basis, conditions=conditions, expected=expected, discrimination=discrimination
        )
        service.bind_prediction(
            current,
            knowledge_cutoff=NOW,
            prespecification_state="A_PRIORI",
            conditions=conditions,
            measurement_contract_ref="artifact:contract",
            assumption_refs=(),
            expected_outcome=expected,
            discrimination_map=discrimination,
            prepared_basis=basis,
        )
    else:
        scope = Mapping({"z": "last", "a": "first"}, events)
        inputs["scope"] = scope
        service.appraise(
            current, evidence_refs=refs, test_assessment_refs=(), appraisal_scope=scope
        )
    return inputs


KEYS = {
    "relation": (
        "relation_revision_id",
        "relation_id",
        "project_id",
        "portfolio_id",
        "source_hypothesis_id",
        "relation_type",
        "target_hypothesis_id",
        "evidence_refs",
        "semantic_role",
        "authority_state",
        "active",
        "created_at",
    ),
    "assumption": (
        "assumption_id",
        "project_id",
        "hypothesis_id",
        "statement",
        "role",
        "evidence_refs",
        "validation_route",
        "status",
        "testability",
        "created_at",
    ),
    "appraisal": (
        "appraisal_id",
        "project_id",
        "hypothesis_id",
        "hypothesis_revision_digest",
        "evidence_refs",
        "test_assessment_refs",
        "appraisal_scope",
        "appraisal",
        "rationale",
        "boundaries",
        "calibration",
        "substantive_update_applied",
        "created_at",
    ),
    "prediction": (
        "prediction_id",
        "project_id",
        "hypothesis_id",
        "hypothesis_revision_digest",
        "knowledge_cutoff",
        "prespecification_state",
        "conditions",
        "population_or_object",
        "time_window",
        "measurement_contract_ref",
        "assumption_refs",
        "expected_outcome",
        "discrimination_map",
        "observation_bound",
        "prediction_fit",
        "created_at",
        "object_id",
        "measurement_contract",
        "measurement_contract_digest",
        "hypothesis_semantic_digest",
        "unbound_assumption_candidates",
        "schema_version",
        "prediction_digest",
    ),
}


@pytest.mark.parametrize(
    "kind", ["relation", "end_relation", "assumption", "prediction", "appraisal"]
)
def test_original_auxiliary_order_and_key_shape(monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    service, events, captured, _error, stop = harness(monkeypatch)
    inputs: dict[str, Any] = {}
    with pytest.raises(DraftCaptured) as raised:
        invoke(service, kind, events, inputs_out=inputs)
    assert raised.value is stop
    draft = captured.get("prediction_draft", captured["draft"])
    assert inputs["current"].model_dump(mode="python") == inputs["before"]
    if kind in {"relation", "assumption", "appraisal"}:
        assert draft["evidence_refs"] is inputs["refs"]
    if kind == "appraisal":
        assert draft["appraisal_scope"] is inputs["scope"]
        assert dict(inputs["scope"]) == {"z": "last", "a": "first"}
    if kind == "end_relation":
        assert inputs["relation"].model_dump(mode="python") == inputs["relation_before"]
    if kind in KEYS:
        assert tuple(draft) == KEYS[kind]
    else:
        assert tuple(draft) == tuple(
            k for k in relation().model_dump(mode="python") if k != "revision_digest"
        )
        assert draft["active"] is False and draft["supersedes_revision_digest"] == "a" * 64
    prefixes = {
        "relation": ["hypothesis-relation-revision", "hypothesis-relation"],
        "end_relation": ["hypothesis-relation-revision"],
        "assumption": ["hypothesis-assumption"],
        "prediction": ["prediction"],
        "appraisal": ["hypothesis-appraisal"],
    }[kind]
    assert [e for e in events if e.startswith("id:") or e == "clock"] == [
        *("id:" + p for p in prefixes),
        "clock",
    ]
    assert draft["created_at"] == NOW
    if kind == "prediction":
        assert events == [
            "id:prediction",
            "get:population",
            "get:time_window",
            "clock",
            "basis:contract",
            "basis:contract_digest",
            "basis:semantic_digest",
            "prediction.validate",
            "scope.items",
            "digest:PREDICTION",
        ]
        assert draft["prediction_fit"] == "NOT_OBSERVED" and draft["observation_bound"] is False
    elif kind == "appraisal":
        assert events == [
            "evidence",
            "bindings",
            "id:hypothesis-appraisal",
            "scope.items",
            "clock",
            "digest:APPRAISAL",
        ]
        assert draft["boundaries"] == ("z=last", "a=first")
        assert draft["appraisal"] == "INCONCLUSIVE" and draft["substantive_update_applied"] is False
    elif kind == "assumption":
        assert draft["status"] == "UNVALIDATED" and draft["testability"] == "NOT_ASSESSED"


@pytest.mark.parametrize("route", [None, "", "calibration record"])
def test_assumption_validation_route_is_not_validation_success(
    monkeypatch: pytest.MonkeyPatch, route: str | None
) -> None:
    service, events, captured, _error, _stop = harness(monkeypatch)
    with pytest.raises(DraftCaptured):
        invoke(service, "assumption", events, validation_route=route)
    assert captured["draft"]["validation_route"] == route
    assert captured["draft"]["testability"] == ("TESTABLE" if route else "NOT_ASSESSED")
    assert captured["draft"]["status"] == "UNVALIDATED"


@pytest.mark.parametrize(
    "kind,prefix",
    [
        ("relation", "hypothesis-relation-revision"),
        ("relation", "hypothesis-relation"),
        ("end_relation", "hypothesis-relation-revision"),
        ("assumption", "hypothesis-assumption"),
        ("prediction", "prediction"),
        ("appraisal", "hypothesis-appraisal"),
    ],
)
@pytest.mark.parametrize("clock_failure", [False, True])
def test_dependency_failures_keep_identity_and_stop_before_digest(
    monkeypatch: pytest.MonkeyPatch, kind: str, prefix: str, clock_failure: bool
) -> None:
    service, events, captured, error, _stop = harness(
        monkeypatch, "clock" if clock_failure else prefix
    )
    with pytest.raises(RuntimeError) as raised:
        invoke(service, kind, events)
    assert raised.value is error
    assert "draft" not in captured and not any(e.startswith("digest:") for e in events)
    assert events[-1] == ("clock" if clock_failure else "id:" + prefix)


def test_prediction_mapping_identity_and_inputs_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    service, events, captured, _error, _stop = harness(monkeypatch)
    current = current_record()
    before = current.model_dump(mode="python")
    conditions = Mapping({"dataset_version": "v1"}, events)
    expected: dict[str, object] = {"measure": "latency", "unit": "ms"}
    mapping: dict[str, object] = {"peers": ["h:2"]}
    old_conditions, old_expected, old_mapping = (
        dict(conditions),
        deepcopy(expected),
        deepcopy(mapping),
    )
    with pytest.raises(DraftCaptured):
        service.bind_prediction(
            current,
            knowledge_cutoff=NOW,
            prespecification_state="A_PRIORI",
            conditions=conditions,
            measurement_contract_ref="contract",
            assumption_refs=(),
            expected_outcome=expected,
            discrimination_map=mapping,
            prepared_basis=make_basis(events),
        )
    draft = captured["prediction_draft"]
    assert (
        draft["conditions"] is conditions
        and draft["expected_outcome"] is expected
        and draft["discrimination_map"] is mapping
    )
    assert (
        dict(conditions) == old_conditions and expected == old_expected and mapping == old_mapping
    )
    assert current.model_dump(mode="python") == before


def test_root_rejections_precede_auxiliary_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    service, events, captured, _error, _stop = harness(monkeypatch)
    with pytest.raises(ValueError, match="already inactive"):
        service.end_relation(portfolio(), relation(False), reason="end", evidence_refs=())
    assert events == ["evidence"] and captured == {}
    events.clear()
    with pytest.raises(ValueError, match="unsupported hypothesis relation"):
        service.add_relation(
            portfolio(),
            source_hypothesis_id="h:1",
            relation_type="UNKNOWN",
            target_hypothesis_id="h:2",
            evidence_refs=(),
            semantic_role=None,
        )
    assert events == []
