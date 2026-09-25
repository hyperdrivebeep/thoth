from typing import Protocol

from thoth.domain.reference import ReferenceCalculation, ReferenceInquiry


class ReferenceCalculatorPort(Protocol):
    @property
    def calculator_id(self) -> str: ...
    @property
    def version(self) -> str: ...
    @property
    def lane(self) -> str: ...
    @property
    def minimum_independent_sources(self) -> int: ...
    def calculate(
        self, inquiry: ReferenceInquiry, input_units: dict[str, str] | None = None
    ) -> ReferenceCalculation: ...


class ReferenceCalculatorRegistryPort(Protocol):
    def resolve(self, calculator_id: str, version: str) -> ReferenceCalculatorPort: ...
