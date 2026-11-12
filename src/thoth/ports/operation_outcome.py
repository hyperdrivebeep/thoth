"""What an operation's reader may add about why it ended, for operations that know their reason."""

from typing import Protocol

from pydantic import JsonValue

from thoth.domain.operation import OperationRecord


class OperationOutcomeReasonPort(Protocol):
    def error_for(self, operation: OperationRecord) -> dict[str, JsonValue] | None:
        """The error to show for a finished operation with none stored, or None for no reason."""
        ...
