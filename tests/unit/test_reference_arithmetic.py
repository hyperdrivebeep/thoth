from decimal import ROUND_DOWN, Decimal, localcontext

import pytest
from pydantic import ValidationError

from thoth.adapters.reference_calculators.registry import (
    ObservedRangeCalculator,
    ReferenceCalculatorRegistry,
    UnitRegistry,
    default_units,
)
from thoth.domain.reference import ReferenceError, ReferenceMeasurement, evaluate_reference_formula


@pytest.mark.parametrize("field", ["quotes", "field_span_ids"])
def test_unknown_context_fields_cannot_introduce_unchecked_source_references(field: str) -> None:
    with pytest.raises(ValidationError, match="REFERENCE_CONTEXT_FIELD_UNKNOWN"):
        ReferenceMeasurement.model_validate(
            {
                "span_id": "span:measurement",
                "value": "120",
                "context": {},
                "quotes": {},
                "value_quote": "120",
                field: {"unrecognized_condition": "span:foreign"},
            }
        )


def test_formula_preserves_decimal_literals_and_ignores_ambient_rounding() -> None:
    with localcontext() as context:
        context.prec = 2
        context.rounding = ROUND_DOWN
        assert evaluate_reference_formula(
            "0.123456789012345678901234567 + n", {"n": Decimal(0)}
        ) == Decimal("0.123456789012345678901234567")
        units = UnitRegistry()
        units.register("one", "length", Decimal(1))
        units.register("six", "length", Decimal(6))
        assert units.convert(Decimal(1), "one", "six") == Decimal("0.1666666666666666666666666667")


@pytest.mark.parametrize(
    "expression", ["n ** 2", "abs(n)", "n.real", "True", "0x10", "n // 2", "unknown + 1"]
)
def test_formula_rejects_unsupported_syntax(expression: str) -> None:
    with pytest.raises(ReferenceError, match="REFERENCE_FORMULA_UNSUPPORTED"):
        evaluate_reference_formula(expression, {"n": Decimal(1)})


@pytest.mark.parametrize("expression", ["n / 0", "1e1024 * 100", "1e2000", "0 / 0"])
def test_formula_rejects_invalid_arithmetic(expression: str) -> None:
    with pytest.raises(ReferenceError, match="REFERENCE_ARITHMETIC_INVALID"):
        evaluate_reference_formula(expression, {"n": Decimal(1)})


def test_formula_and_registry_bounds() -> None:
    with pytest.raises(ReferenceError, match="REFERENCE_FORMULA_LIMIT"):
        evaluate_reference_formula("n", {"n": Decimal("NaN")})
    with pytest.raises(ReferenceError, match="REFERENCE_FORMULA_LIMIT"):
        evaluate_reference_formula("1+" * 150 + "1", {})
    calculator = ObservedRangeCalculator(default_units())
    registry = ReferenceCalculatorRegistry((calculator,))
    assert registry.resolve("observed-range", "1.0.0") is calculator
    with pytest.raises(ValueError, match="REFERENCE_CALCULATOR_DUPLICATE"):
        registry.register(calculator)
    with pytest.raises(ReferenceError, match="REFERENCE_CALCULATOR_UNAVAILABLE"):
        registry.resolve("observed-range", "2.0.0")
    with pytest.raises(ReferenceError, match="REFERENCE_UNIT_INCOMPATIBLE"):
        default_units().convert(Decimal(1), "ms", "kg")
