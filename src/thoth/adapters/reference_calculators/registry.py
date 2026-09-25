"""Versioned deterministic calculators; no model/provider/metric-name branches."""

from collections.abc import Iterable
from decimal import ROUND_HALF_EVEN, Context, Decimal, DecimalException, localcontext

from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.reference import (
    CONTEXT_FIELDS,
    ReferenceCalculation,
    ReferenceError,
    ReferenceInquiry,
    evaluate_reference_formula,
)
from thoth.ports.reference_calculator import ReferenceCalculatorPort


class UnitRegistry:
    def __init__(self) -> None:
        self._units: dict[str, tuple[str, Decimal]] = {}

    def register(self, name: str, dimension: str, scale: Decimal) -> None:
        if name in self._units or not scale.is_finite() or scale <= 0:
            raise ValueError("REFERENCE_UNIT_REGISTRATION_INVALID")
        self._units[name] = (dimension, scale)

    def convert(self, value: Decimal, source: str, target: str) -> Decimal:
        if source not in self._units or target not in self._units:
            raise ReferenceError("REFERENCE_UNIT_UNKNOWN")
        left, right = self._units[source], self._units[target]
        if left[0] != right[0]:
            raise ReferenceError("REFERENCE_UNIT_INCOMPATIBLE")
        with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN, Emin=-1024, Emax=1024)):
            return value * left[1] / right[1]


def default_units() -> UnitRegistry:
    result = UnitRegistry()
    for name, dimension, scale in (
        ("ms", "time", "0.001"),
        ("s", "time", "1"),
        ("g", "mass", "0.001"),
        ("kg", "mass", "1"),
        ("m", "length", "1"),
        ("km", "length", "1000"),
        ("count", "count", "1"),
        ("ratio", "ratio", "1"),
        ("%", "ratio", "0.01"),
    ):
        result.register(name, dimension, Decimal(scale))
    return result


class ObservedRangeCalculator:
    calculator_id = "observed-range"
    version = "1.0.0"
    lane = "REFERENCE_RANGE_CANDIDATE"
    minimum_independent_sources = 2

    def __init__(self, units: UnitRegistry) -> None:
        self._units = units

    def calculate(
        self, inquiry: ReferenceInquiry, input_units: dict[str, str] | None = None
    ) -> ReferenceCalculation:
        del input_units
        target = inquiry.target.unit
        if target is None or len(inquiry.measurements) < 2:
            raise ReferenceError("REFERENCE_COMPARABLE_OBSERVATIONS_REQUIRED")
        try:
            with localcontext() as context:
                context.prec = 28
                values = tuple(
                    self._units.convert(m.value, m.context.unit or "", target)
                    for m in inquiry.measurements
                )
        except DecimalException:
            raise ReferenceError("REFERENCE_ARITHMETIC_INVALID") from None
        payload = {
            "normalized_values": tuple(str(v) for v in values),
            "lower": str(min(values)),
            "upper": str(max(values)),
            "unit": target,
            "operations": (
                "unit-scale-conversion",
                "observed-min-max",
                "decimal:28:ROUND_HALF_EVEN",
            ),
        }
        return ReferenceCalculation(
            calculator_id=self.calculator_id,
            calculator_version=self.version,
            input_digest=domain_digest(
                "REFERENCE_CALCULATOR_INPUT",
                "1.0.0",
                canonical_payload(
                    {
                        "target": inquiry.target,
                        "measurements": inquiry.measurements,
                        "source_refs": inquiry.source_refs,
                    }
                ),
            ),
            output_digest=domain_digest(
                "REFERENCE_CALCULATOR_OUTPUT", "1.0.0", canonical_payload(payload)
            ),
            source_refs=measurement_source_refs(inquiry),
            uncertainty="Observed values only; not a confidence interval or official target.",
            normalized_values=tuple(str(v) for v in values),
            lower=str(min(values)),
            upper=str(max(values)),
            unit=target,
            operations=("unit-scale-conversion", "observed-min-max", "decimal:28:ROUND_HALF_EVEN"),
        )


class ApprovedFormulaCalculator:
    calculator_id = "approved-formula"
    version = "1.0.0"
    lane = "RECALCULATED_VALUE"
    minimum_independent_sources = 1

    def __init__(self, units: UnitRegistry) -> None:
        self._units = units

    def calculate(
        self, inquiry: ReferenceInquiry, input_units: dict[str, str] | None = None
    ) -> ReferenceCalculation:
        if not input_units or not inquiry.target.formula or not inquiry.target.unit:
            raise ReferenceError("REFERENCE_FORMULA_INPUT_CONTRACT_MISSING")
        variables: dict[str, Decimal] = {}
        try:
            with localcontext() as context:
                context.prec = 28
                for measurement in inquiry.measurements:
                    name = measurement.variable
                    if name is None or name in variables or name not in input_units:
                        raise ReferenceError("REFERENCE_FORMULA_VARIABLES_MISMATCH")
                    variables[name] = self._units.convert(
                        measurement.value, measurement.context.unit or "", input_units[name]
                    )
        except DecimalException:
            raise ReferenceError("REFERENCE_ARITHMETIC_INVALID") from None
        if set(variables) != set(input_units):
            raise ReferenceError("REFERENCE_FORMULA_VARIABLES_MISMATCH")
        result = evaluate_reference_formula(inquiry.target.formula, variables)
        values = {key: str(variables[key]) for key in sorted(variables)}
        output = {"value": str(result), "unit": inquiry.target.unit, "variables": values}
        return ReferenceCalculation(
            calculator_id=self.calculator_id,
            calculator_version=self.version,
            input_digest=domain_digest(
                "REFERENCE_CALCULATOR_INPUT",
                "1.0.0",
                canonical_payload(
                    {
                        "formula": inquiry.target.formula,
                        "measurements": inquiry.measurements,
                        "input_units": input_units,
                        "binding": inquiry.evaluator_binding_digest,
                    }
                ),
            ),
            output_digest=domain_digest(
                "REFERENCE_CALCULATOR_OUTPUT", "1.0.0", canonical_payload(output)
            ),
            normalized_values=tuple(values.values()),
            variables=values,
            value=str(result),
            unit=inquiry.target.unit,
            source_refs=measurement_source_refs(inquiry),
            operations=("approved-formula", "unit-scale-conversion", "decimal:28:ROUND_HALF_EVEN"),
            uncertainty="Arithmetic only; no measurement-validity or official-disposition claim.",
        )


def measurement_source_refs(inquiry: ReferenceInquiry) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            ref
            for measurement in inquiry.measurements
            for ref in (
                measurement.span_id,
                *(
                    measurement.field_span_ids.get(field, measurement.span_id)
                    for field in CONTEXT_FIELDS
                ),
            )
        )
    )


class ReferenceCalculatorRegistry:
    def __init__(self, calculators: Iterable[ReferenceCalculatorPort] = ()) -> None:
        self._calculators: dict[tuple[str, str], ReferenceCalculatorPort] = {}
        for calculator in calculators:
            self.register(calculator)

    def register(self, calculator: ReferenceCalculatorPort) -> None:
        key = (calculator.calculator_id, calculator.version)
        if key in self._calculators:
            raise ValueError("REFERENCE_CALCULATOR_DUPLICATE")
        self._calculators[key] = calculator

    def resolve(self, calculator_id: str, version: str) -> ReferenceCalculatorPort:
        try:
            return self._calculators[(calculator_id, version)]
        except KeyError:
            raise ReferenceError("REFERENCE_CALCULATOR_UNAVAILABLE") from None


def default_reference_calculators() -> ReferenceCalculatorRegistry:
    units = default_units()
    return ReferenceCalculatorRegistry(
        (ObservedRangeCalculator(units), ApprovedFormulaCalculator(units))
    )
