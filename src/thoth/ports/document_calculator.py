"""A low-authority arithmetic capability, independent of ReferenceInquiry."""

from typing import Protocol

from thoth.domain.derived_calculation import DerivedCalculation
from thoth.domain.numeric_series import NumericSeriesObservation


class DocumentCalculatorPort(Protocol):
    @property
    def version(self) -> str: ...
    @property
    def unit_registry_digest(self) -> str: ...
    def supports_unit(self, unit: str) -> bool: ...
    def calculate(self, series: NumericSeriesObservation) -> tuple[DerivedCalculation, ...]: ...
