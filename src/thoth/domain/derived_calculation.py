"""Arithmetic records stay separate from original evidence and official criteria."""

from typing import Literal

from pydantic import model_validator

from thoth.domain.base import DomainModel
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.ids import Sha256
from thoth.domain.numeric_series import NumericDiagnostic, NumericPoint, NumericSeriesObservation
from thoth.domain.research_request import RevisionRef

CALCULATION_VERSION = "adjacent-series:1.0.0"
SOURCE_ARITHMETIC_CONTRACT = (
    "Values calculated from source numbers; not a causal or computational-complexity determination."
)
NUMERIC_CITATION_INSTRUCTION = (
    "When checked_observations are present, cite the step ratios and log-log slopes of the series "
    "that bear on the question as observed arithmetic with their operand refs, and compare slopes "
    "between series where relevant; do not state a cause or a complexity class from them."
)


def calculation_input_digest(
    series: NumericSeriesObservation,
    operands: tuple[NumericPoint, NumericPoint],
    operation: str,
    version: str,
) -> str:
    return domain_digest(
        "NUMERIC_CALC_INPUT",
        "1.0.0",
        canonical_payload(
            {
                "series": series.series_digest,
                "points": series.points_digest,
                "binding": series.binding_digest,
                "units": series.unit_registry_digest,
                "operands": operands,
                "operation": operation,
                "version": version,
                "precision": 28,
                "rounding": "ROUND_HALF_EVEN",
            }
        ),
    )


class DerivedCalculation(DomainModel):
    record_kind: Literal["DerivedCalculation"] = "DerivedCalculation"
    schema_version: Literal["1.0.0"] = "1.0.0"
    calculator_version: str = CALCULATION_VERSION
    operation: Literal["RATIO", "DIFFERENCE", "LOG_LOG_SLOPE"]
    series_digest: Sha256
    points_digest: Sha256
    binding_digest: Sha256
    unit_registry_digest: Sha256
    operand_refs: tuple[str, str]
    sizes: tuple[str, str]
    values: tuple[str, str]
    unit: str
    precision: Literal[28] = 28
    rounding: Literal["ROUND_HALF_EVEN"] = "ROUND_HALF_EVEN"
    status: Literal["CALCULATED", "NOT_CALCULATED"]
    value: str | None = None
    display_value: str | None = None
    reason: str | None = None
    input_digest: Sha256
    output_digest: Sha256
    authority: Literal["DERIVED_OBSERVATION_ONLY"] = "DERIVED_OBSERVATION_ONLY"

    @model_validator(mode="after")
    def explicit_outcome(self) -> "DerivedCalculation":
        if self.status == "CALCULATED":
            if self.value is None or self.display_value is None or self.reason is not None:
                raise ValueError("NUMERIC_CALCULATION_OUTCOME_INVALID")
        elif self.value is not None or self.display_value is not None or self.reason is None:
            raise ValueError("NUMERIC_CALCULATION_OUTCOME_INVALID")
        if self.output_digest != domain_digest(
            "NUMERIC_CALC_OUTPUT",
            "1.0.0",
            canonical_payload(
                {
                    "value": self.value,
                    "display_value": self.display_value,
                    "reason": self.reason,
                }
            ),
        ):
            raise ValueError("NUMERIC_OUTPUT_DIGEST_MISMATCH")
        return self


class NumericDelivery(DomainModel):
    role: str
    delivered: int
    not_delivered: int
    reasons: tuple[str, ...] = ()


class CheckedObservations(DomainModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    kind: Literal["SOURCE_ARITHMETIC"] = "SOURCE_ARITHMETIC"
    contract: Literal[
        "Values calculated from source numbers; "
        "not a causal or computational-complexity determination."
    ] = SOURCE_ARITHMETIC_CONTRACT
    request_ref: RevisionRef
    series: tuple[NumericSeriesObservation, ...]
    calculations: tuple[DerivedCalculation, ...]
    diagnostics: tuple[NumericDiagnostic, ...] = ()
    deliveries: tuple[NumericDelivery, ...] = ()

    @model_validator(mode="after")
    def bound_calculations(self) -> "CheckedObservations":
        series = {s.binding_digest: s for s in self.series}
        if len(series) != len(self.series) or any(
            s.project_id != self.request_ref.project_id for s in self.series
        ):
            raise ValueError("NUMERIC_SERIES_SCOPE_INVALID")
        for calculation in self.calculations:
            source = series.get(calculation.binding_digest)
            if source is None or (
                calculation.series_digest,
                calculation.points_digest,
                calculation.unit_registry_digest,
            ) != (source.series_digest, source.points_digest, source.unit_registry_digest):
                raise ValueError("NUMERIC_CALCULATION_BINDING_MISMATCH")
            points = {p.source.span_id: p for p in source.points}
            if any(ref not in points for ref in calculation.operand_refs):
                raise ValueError("NUMERIC_OPERAND_MISSING")
            operands = (
                points[calculation.operand_refs[0]],
                points[calculation.operand_refs[1]],
            )
            if calculation.sizes != tuple(p.size for p in operands) or calculation.values != tuple(
                p.value for p in operands
            ):
                raise ValueError("NUMERIC_OPERAND_MISMATCH")
            if calculation.input_digest != calculation_input_digest(
                source, operands, calculation.operation, calculation.calculator_version
            ):
                raise ValueError("NUMERIC_INPUT_DIGEST_MISMATCH")
            expected_unit = source.unit if calculation.operation == "DIFFERENCE" else "ratio"
            if calculation.unit != expected_unit:
                raise ValueError("NUMERIC_OUTPUT_UNIT_MISMATCH")
        return self
