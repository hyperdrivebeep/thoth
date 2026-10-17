"""Direct tests of the pure Reference record construction and checks (97, added after the move)."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pytest

from thoth.application.services.reference_grounding import (
    check_measurement_grounding,
    eligible,
    formula_inputs,
)
from thoth.application.services.reference_inquiry_drafts import (
    calculator_request,
    merged_traces,
    operator_actor,
    question_set,
    scenario_deltas,
    selected_calculator,
    source_basis_digest,
)
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import (
    AuthorityState,
    CutoffState,
    SupportState,
    VerificationState,
)
from thoth.domain.reference import ReferenceContext, ReferenceError, ReferenceMeasurement

CONTEXT = {
    "metric_definition": "latency",
    "formula": "elapsed time",
    "unit": "ms",
    "denominator": "per-request",
    "population": "declared test population",
    "environment": "controlled test environment",
    "time_window": "trial-v1",
    "measurement_method": "elapsed-time observation",
}


def ns(**values: Any) -> Any:
    return cast(Any, SimpleNamespace(**values))


def good_state(**over: Any) -> Any:
    base: dict[str, Any] = {
        "cutoff_state": CutoffState.ELIGIBLE,
        "authority_state": AuthorityState.OFFICIAL,
        "support_state": SupportState.SUPPORTED,
        "verification_state": VerificationState.SCHEMA_VALID,
    }
    return ns(**{**base, **over})


def code(call: Any) -> str:
    with pytest.raises(ReferenceError) as caught:
        call()
    return caught.value.code


def test_source_eligibility_names_the_first_failing_state() -> None:
    eligible(good_state(), good_state())
    assert code(
        lambda: eligible(good_state(cutoff_state=CutoffState.AFTER_CUTOFF), good_state())
    ) == ("REFERENCE_SOURCE_CUTOFF_INELIGIBLE")
    assert code(
        lambda: eligible(good_state(), good_state(cutoff_state=CutoffState.AFTER_CUTOFF))
    ) == ("REFERENCE_SOURCE_CUTOFF_INELIGIBLE")
    assert code(
        lambda: eligible(good_state(authority_state=AuthorityState.UNCLASSIFIED), good_state())
    ) == ("REFERENCE_SOURCE_AUTHORITY_UNRESOLVED")
    assert code(lambda: eligible(good_state(support_state=SupportState.REFUTED), good_state())) == (
        "REFERENCE_SOURCE_SUPPORT_UNRESOLVED"
    )
    assert code(
        lambda: eligible(good_state(verification_state=VerificationState.NOT_CHECKED), good_state())
    ) == ("REFERENCE_SOURCE_PROVENANCE_UNRESOLVED")


def test_formula_inputs_require_an_authorized_bound_formula_contract() -> None:
    target = ReferenceContext.model_validate({**CONTEXT, "formula": "n / d", "unit": "%"})
    spec = {
        "type": "FORMULA",
        "expression": "n / d",
        "unit": "%",
        "input_units": {"n": "count", "d": "count"},
        "evaluator_binding_digest": "b" * 64,
    }

    def current(**over: Any) -> Any:
        return ns(usage_authorization="AUTHORIZED_EVALUATOR_INPUT", computation_spec=spec, **over)

    assert formula_inputs(current(), target, "b" * 64) == {"n": "count", "d": "count"}
    assert code(
        lambda: formula_inputs(
            ns(usage_authorization="NOT_AUTHORIZED", computation_spec=spec), target, "b" * 64
        )
    ) == ("REFERENCE_FORMULA_NOT_AUTHORIZED")
    assert code(lambda: formula_inputs(current(), target, None)) == (
        "REFERENCE_FORMULA_BINDING_MISMATCH"
    )
    other = ReferenceContext.model_validate({**CONTEXT, "formula": "n * d", "unit": "%"})
    assert code(lambda: formula_inputs(current(), other, "b" * 64)) == (
        "REFERENCE_FORMULA_TARGET_MISMATCH"
    )
    empty = ns(
        usage_authorization="AUTHORIZED_EVALUATOR_INPUT",
        computation_spec={**spec, "input_units": {}},
    )
    assert code(lambda: formula_inputs(empty, target, "b" * 64)) == (
        "REFERENCE_FORMULA_INPUT_CONTRACT_MISSING"
    )
    blank = ns(
        usage_authorization="AUTHORIZED_EVALUATOR_INPUT",
        computation_spec={**spec, "input_units": {"n": ""}},
    )
    assert code(lambda: formula_inputs(blank, target, "b" * 64)) == (
        "REFERENCE_FORMULA_INPUT_CONTRACT_MISSING"
    )


def test_merged_traces_keep_the_prior_and_add_a_new_trace_once() -> None:
    first, second = object(), object()
    inquiry = ns(mapping_traces=(first,))
    assert merged_traces(None, None) == ()
    assert merged_traces(inquiry, None) == (first,)
    assert merged_traces(inquiry, cast(Any, first)) == (first,)
    assert merged_traces(inquiry, cast(Any, second)) == (first, second)
    assert merged_traces(None, cast(Any, second)) == (second,)


def test_scenario_deltas_subtract_the_base_bounds_when_both_exist() -> None:
    calculated = ns(lower="100", upper="140")
    assert scenario_deltas(calculated, ns(lower="120", upper="160")) == ("-20", "-20")
    assert scenario_deltas(ns(lower=None, upper="140"), ns(lower="120", upper="160")) == (
        None,
        "-20",
    )
    assert scenario_deltas(calculated, ns(lower="120", upper=None)) == ("-20", None)


class Ids:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def new(self, prefix: str) -> str:
        self.calls.append(prefix)
        return f"{prefix}:{len(self.calls)}"


def test_question_set_reuses_existing_questions_and_numbers_the_new_ones_in_order() -> None:
    kept = ns(field="unit")
    inquiry = ns(
        questions=(kept,),
        question_order=("population", "unit", "not-a-field"),
        original_revision_digest="d" * 64,
    )
    ids = Ids()
    questions = question_set(inquiry, ("denominator", "population"), cast(Any, ids))
    assert [q.field for q in questions] == ["unit", "population", "denominator"]
    assert questions[0] is kept
    assert ids.calls == ["reference-question", "reference-question"]
    assert [q.question_id for q in questions[1:]] == [
        "reference-question:1",
        "reference-question:2",
    ]
    assert all(q.basis_revision_digest == "d" * 64 for q in questions[1:])
    assert (
        question_set(
            ns(questions=(), question_order=(), original_revision_digest="d" * 64),
            (),
            cast(Any, Ids()),
        )
        == ()
    )


def test_operator_actor_is_the_local_operator_without_an_authenticated_actor() -> None:
    current = ns(project_id="project:x")
    local = operator_actor(current, None)
    assert (local.actor_id, local.role, local.session_id) == (
        "local:operator",
        "local-operator",
        None,
    )
    actor = ns(actor_id="human:a", role="reviewer", session_id="s1", role_assignment_id="ra1")
    named = operator_actor(current, actor)
    assert (named.actor_id, named.role, named.session_id) == ("human:a", "reviewer", "s1")
    assert named.role_assignment_ref == "ra1" and named.project_id == "project:x"


def test_source_basis_digest_is_the_domain_digest_of_span_and_artifact() -> None:
    span, artifact = {"span": 1}, {"artifact": 2}
    assert source_basis_digest(cast(Any, span), cast(Any, artifact)) == domain_digest(
        "REFERENCE_SOURCE_BASIS", "1.0.0", canonical_payload({"span": span, "artifact": artifact})
    )


def test_selected_calculator_parses_name_version_and_lane() -> None:
    assert selected_calculator(ns(scope={"reference_calculator": "observed-range@1.0.0"})) == (
        "observed-range",
        "1.0.0",
        "REFERENCE_RANGE_CANDIDATE",
    )
    formula = {"reference_calculator": "approved-formula@2", "reference_lane": "RECALCULATED_VALUE"}
    assert selected_calculator(ns(scope=formula)) == ("approved-formula", "2", "RECALCULATED_VALUE")
    for bad in ("", "observed-range", "@1.0.0", "observed-range@"):
        assert code(
            lambda bad=bad: selected_calculator(ns(scope={"reference_calculator": bad}))
        ) == ("REFERENCE_CALCULATOR_SELECTION_REQUIRED")
    invalid = {"reference_calculator": "a@1", "reference_lane": "OTHER"}
    assert code(lambda: selected_calculator(ns(scope=invalid))) == "REFERENCE_LANE_INVALID"


def test_calculator_request_binds_only_a_string_evaluator_digest() -> None:
    criterion = ns(
        criterion_id="criterion:1",
        revision_digest="a" * 64,
        required_evidence=("span:1",),
        context_spec={**CONTEXT, "extra": "dropped"},
    )
    request = calculator_request(
        criterion, "RECALCULATED_VALUE", "approved-formula", "1.0.0", "b" * 64
    )
    assert request.lane == "RECALCULATED_VALUE" and request.evaluator_binding_digest == "b" * 64
    assert request.source_refs == ("span:1",) and request.target.unit == "ms"
    assert (
        calculator_request(
            criterion, "REFERENCE_RANGE_CANDIDATE", "c", "1", None
        ).evaluator_binding_digest
        is None
    )
    assert (
        calculator_request(
            criterion, "REFERENCE_RANGE_CANDIDATE", "c", "1", 5
        ).evaluator_binding_digest
        is None
    )


def measurement(**over: Any) -> ReferenceMeasurement:
    quotes = {field: f"{field}: {text}" for field, text in CONTEXT.items()}
    base: dict[str, Any] = {
        "span_id": "span:v",
        "value": Decimal("120"),
        "context": CONTEXT,
        "quotes": quotes,
        "field_span_ids": {field: "span:v" for field in CONTEXT},
        "value_quote": "value: 120 ms",
        "variable": None,
    }
    return ReferenceMeasurement.model_validate({**base, **over})


def grounded(**over: Any) -> tuple[Any, Any, Any, dict[str, Any], dict[str, Any]]:
    text = "\n".join([*(f"{field}: {value}" for field, value in CONTEXT.items()), "value: 120 ms"])
    span = ns(artifact_id="artifact:1", exact_text=text, **vars(good_state()))
    artifact = ns(**vars(good_state()))
    inquiry = ns(lane=over.pop("lane", "REFERENCE_RANGE_CANDIDATE"))
    return inquiry, span, artifact, {"span:v": span}, dict(CONTEXT)


def check(m: ReferenceMeasurement, **over: Any) -> None:
    inquiry, span, artifact, spans, target = grounded(**over)
    check_measurement_grounding(inquiry, m, span, artifact, spans, target)


def test_measurement_grounding_accepts_a_fully_quoted_measurement() -> None:
    check(measurement())


def test_measurement_grounding_names_each_missing_grounding() -> None:
    quotes = {field: f"{field}: {text}" for field, text in CONTEXT.items()}
    assert code(lambda: check(measurement(quotes={**quotes, "unit": ""}))) == (
        "REFERENCE_FIELD_GROUNDING_MISSING"
    )
    assert code(lambda: check(measurement(field_span_ids={"unit": "span:other"}))) == (
        "REFERENCE_FIELD_GROUNDING_MISSING"
    )
    other = {**CONTEXT, "denominator": "per-user"}
    mismatched = measurement(
        context=other, quotes={**quotes, "denominator": "denominator: per-user"}
    )
    assert code(lambda: check(mismatched)) == "REFERENCE_FIELD_GROUNDING_MISSING"
    assert code(lambda: check(measurement(value_quote="value: 999 ms"))) == (
        "REFERENCE_VALUE_GROUNDING_MISSING"
    )
    assert code(
        lambda: check(measurement(value_quote="value: 120 ms"), lane="RECALCULATED_VALUE")
    ) == ("REFERENCE_VARIABLE_GROUNDING_MISSING")
    check(measurement(variable="value"), lane="RECALCULATED_VALUE")
