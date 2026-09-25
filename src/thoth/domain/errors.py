from __future__ import annotations

from thoth.domain.enums import ParserErrorCode


class DomainError(ValueError):
    """Base error for rejected domain state."""


class CanonicalizationError(DomainError):
    """Value cannot participate in a canonical digest."""


class InvariantViolation(DomainError):
    """Cross-field domain invariant failed."""


class ParserFailure(DomainError):
    """A document cannot be safely represented as a structural document."""

    def __init__(self, code: ParserErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
