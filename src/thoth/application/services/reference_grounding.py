"""Pure checks for Reference sources, formulas and measurement grounding (97).

These functions only judge values the service has already read: the evaluator formula contract,
whether a source is eligible, and whether one measurement is grounded in its cited spans. They read
nothing and write nothing; the service keeps the evidence and artifact reads and the scope checks
and calls these checks at the same point of the same sequence.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, cast

from thoth.domain.artifact import ArtifactEnvelope
from thoth.domain.criterion_contract import CriterionContractRecord
from thoth.domain.enums import AuthorityState, CutoffState, SupportState, VerificationState
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.reference import (
    CONTEXT_FIELDS,
    ReferenceContext,
    ReferenceError,
    ReferenceInquiry,
    ReferenceMeasurement,
)


def formula_inputs(
    current: CriterionContractRecord, target: ReferenceContext, binding: str | None
) -> dict[str, str]:
    computation = current.computation_spec or {}
    if (
        current.usage_authorization != "AUTHORIZED_EVALUATOR_INPUT"
        or computation.get("type") != "FORMULA"
    ):
        raise ReferenceError("REFERENCE_FORMULA_NOT_AUTHORIZED")
    if binding is None or binding != computation.get("evaluator_binding_digest"):
        raise ReferenceError("REFERENCE_FORMULA_BINDING_MISMATCH")
    if target.formula != computation.get("expression") or target.unit != computation.get("unit"):
        raise ReferenceError("REFERENCE_FORMULA_TARGET_MISMATCH")
    units = computation.get("input_units")
    if not isinstance(units, dict) or not units:
        raise ReferenceError("REFERENCE_FORMULA_INPUT_CONTRACT_MISSING")
    values = cast(dict[object, object], units)
    if any(
        not isinstance(k, str) or not isinstance(v, str) or not k or not v
        for k, v in values.items()
    ):
        raise ReferenceError("REFERENCE_FORMULA_INPUT_CONTRACT_MISSING")
    return {str(k): str(v) for k, v in values.items()}


def eligible(span: EvidenceSpan, artifact: ArtifactEnvelope) -> None:
    if span.cutoff_state != CutoffState.ELIGIBLE or artifact.cutoff_state != CutoffState.ELIGIBLE:
        raise ReferenceError("REFERENCE_SOURCE_CUTOFF_INELIGIBLE")
    if span.authority_state not in {
        AuthorityState.INFORMAL,
        AuthorityState.OFFICIAL,
        AuthorityState.APPROVED,
    }:
        raise ReferenceError("REFERENCE_SOURCE_AUTHORITY_UNRESOLVED")
    if span.support_state not in {
        SupportState.EXTRACTED,
        SupportState.SUPPORTED_CANDIDATE,
        SupportState.SUPPORTED,
    }:
        raise ReferenceError("REFERENCE_SOURCE_SUPPORT_UNRESOLVED")
    if span.verification_state not in {
        VerificationState.SCHEMA_VALID,
        VerificationState.PROVENANCE_VALID,
        VerificationState.DETERMINISTICALLY_VERIFIED,
    }:
        raise ReferenceError("REFERENCE_SOURCE_PROVENANCE_UNRESOLVED")


def check_measurement_grounding(
    inquiry: ReferenceInquiry,
    measurement: ReferenceMeasurement,
    span: EvidenceSpan,
    artifact: ArtifactEnvelope,
    spans: dict[str, EvidenceSpan],
    target: dict[str, Any],
) -> None:
    eligible(span, artifact)
    fields = measurement.context.model_dump()
    for field in CONTEXT_FIELDS:
        text = fields[field]
        quote = measurement.quotes.get(field)
        field_span = spans.get(measurement.field_span_ids.get(field, measurement.span_id))
        if (
            field_span is None
            or field_span.artifact_id != span.artifact_id
            or not text
            or not quote
            or quote not in field_span.exact_text
            or text not in quote
        ):
            raise ReferenceError("REFERENCE_FIELD_GROUNDING_MISSING")
        eligible(field_span, artifact)
        if field != "unit" and text.strip().casefold() != str(target[field]).strip().casefold():
            raise ReferenceError("REFERENCE_CONTEXT_INCOMPATIBLE")
    if measurement.value_quote not in span.exact_text:
        raise ReferenceError("REFERENCE_VALUE_GROUNDING_MISSING")
    numbers = re.findall(
        r"(?<![\w.])[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?(?![\w.])",
        measurement.value_quote,
    )
    if {Decimal(n) for n in numbers} != {measurement.value}:
        raise ReferenceError("REFERENCE_VALUE_GROUNDING_MISSING")
    if inquiry.lane == "RECALCULATED_VALUE" and (
        measurement.variable is None
        or re.search(
            r"(?<!\w)" + re.escape(measurement.variable) + r"(?!\w)",
            measurement.value_quote,
        )
        is None
    ):
        raise ReferenceError("REFERENCE_VARIABLE_GROUNDING_MISSING")
