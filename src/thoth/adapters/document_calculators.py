"""Deterministic adjacent-pair arithmetic using the existing unit registry."""

from collections.abc import Iterable
from decimal import ROUND_HALF_EVEN, Context, Decimal, DecimalException, Underflow, localcontext
from itertools import pairwise
from typing import Literal

from thoth.adapters.reference_calculators.registry import UnitRegistry, default_units
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.derived_calculation import (
    CALCULATION_VERSION,
    DerivedCalculation,
    calculation_input_digest,
)
from thoth.domain.numeric_series import NumericPoint, NumericSeriesObservation
from thoth.domain.reference import ReferenceError
from thoth.ports.document_calculator import DocumentCalculatorPort


class AdjacentSeriesCalculator:
    version = CALCULATION_VERSION

    def __init__(self, units: UnitRegistry) -> None:
        self.units = units

    @property
    def unit_registry_digest(self) -> str:
        return domain_digest(
            "NUMERIC_UNITS", "1.0.0", canonical_payload({"units": self.units.manifest()})
        )

    def supports_unit(self, unit: str) -> bool:
        try:
            self.units.convert(Decimal(1), unit, unit)
        except ReferenceError:
            return False
        return True

    def calculate(self, series: NumericSeriesObservation) -> tuple[DerivedCalculation, ...]:
        if series.unit_registry_digest != self.unit_registry_digest:
            raise ValueError("NUMERIC_UNIT_BASIS_CHANGED")
        ordered = sorted(series.points, key=lambda p: Decimal(p.size))
        if len({Decimal(p.size) for p in ordered}) != len(ordered):
            raise ValueError("NUMERIC_DUPLICATE_SIZE")
        return tuple(
            self._pair(series, left, right, operation)
            for left, right in pairwise(ordered)
            for operation in ("RATIO", "DIFFERENCE", "LOG_LOG_SLOPE")
        )

    def _pair(
        self,
        series: NumericSeriesObservation,
        left: NumericPoint,
        right: NumericPoint,
        operation: Literal["RATIO", "DIFFERENCE", "LOG_LOG_SLOPE"],
    ) -> DerivedCalculation:
        value = display = reason = None
        result: Decimal | None = None
        try:
            arithmetic = Context(prec=28, rounding=ROUND_HALF_EVEN, Emin=-1024, Emax=1024)
            arithmetic.traps[Underflow] = True
            with localcontext(arithmetic):
                x1, x2 = Decimal(left.size), Decimal(right.size)
                y1 = self.units.convert(Decimal(left.value), series.unit, series.unit)
                y2 = self.units.convert(Decimal(right.value), series.unit, series.unit)
                if operation == "RATIO":
                    if y1 == 0:
                        reason = "ZERO_DENOMINATOR"
                    else:
                        result = y2 / y1
                elif operation == "DIFFERENCE":
                    result = y2 - y1
                elif x1 <= 0 or x2 <= 0 or y1 <= 0 or y2 <= 0 or x1 == x2:
                    reason = "LOG_DOMAIN_INVALID"
                else:
                    result = (y2 / y1).ln() / (x2 / x1).ln()
                if result is not None and not result.is_finite():
                    reason = "ARITHMETIC_OR_UNIT_INVALID"
                if reason is None and result is not None:
                    value = str(result)
                    # Fixed six decimal places; formatting uses the same HALF_EVEN context.
                    display = format(result, ".6f")
                elif reason is None:
                    raise ValueError("NUMERIC_RESULT_MISSING")
        except (DecimalException, ReferenceError):
            reason = "ARITHMETIC_OR_UNIT_INVALID"
            value = display = None
        output = {"value": value, "display_value": display, "reason": reason}
        return DerivedCalculation(
            calculator_version=self.version,
            operation=operation,
            series_digest=series.series_digest,
            points_digest=series.points_digest,
            binding_digest=series.binding_digest,
            operand_refs=(left.source.span_id, right.source.span_id),
            unit_registry_digest=self.unit_registry_digest,
            sizes=(left.size, right.size),
            values=(left.value, right.value),
            unit=series.unit if operation == "DIFFERENCE" else "ratio",
            status="CALCULATED" if reason is None else "NOT_CALCULATED",
            value=value,
            display_value=display,
            reason=reason,
            input_digest=calculation_input_digest(series, (left, right), operation, self.version),
            output_digest=domain_digest("NUMERIC_CALC_OUTPUT", "1.0.0", canonical_payload(output)),
        )


class DocumentCalculatorRegistry:
    def __init__(self, calculators: Iterable[DocumentCalculatorPort]) -> None:
        self._calculators: dict[str, DocumentCalculatorPort] = {}
        for calculator in calculators:
            if calculator.version in self._calculators:
                raise ValueError("DOCUMENT_CALCULATOR_DUPLICATE")
            self._calculators[calculator.version] = calculator

    def resolve(self, version: str) -> DocumentCalculatorPort:
        if version not in self._calculators:
            raise ValueError("DOCUMENT_CALCULATOR_UNAVAILABLE")
        return self._calculators[version]


def default_document_calculator() -> DocumentCalculatorPort:
    return DocumentCalculatorRegistry((AdjacentSeriesCalculator(default_units()),)).resolve(
        CALCULATION_VERSION
    )
