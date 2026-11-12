"""That a person stopped a research request, kept apart from every other way one ends cancelled.

A research request can end CANCELLED because the person pressed stop, because the time ran out or
because the server was shut down. Only the first is recorded here, so a screen can say so; the
other endings keep an unknown reason. The operation row itself is not touched: an output of an
operation needs a resource binding, which a cancelled operation does not have.
"""

from typing import Literal

from pydantic import JsonValue

from thoth.domain.base import DomainModel

RESEARCH_EXECUTION_NAMESPACE = "RESEARCH_EXECUTION"


class ResearchStopNote(DomainModel):
    record_kind: Literal["ResearchStopNote"] = "ResearchStopNote"
    schema_version: Literal["1.0.0"] = "1.0.0"
    operation_id: str
    reason: Literal["USER_STOPPED"] = "USER_STOPPED"
    requested_at: str


def stop_note_key(operation_id: str) -> str:
    return f"stop:{operation_id}"


def user_stopped_error(note: ResearchStopNote) -> dict[str, JsonValue]:
    """The reason in the shape a screen already reads an operation's error in."""
    return {"message": note.reason, "data": {"reason_code": note.reason}}
