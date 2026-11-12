"""The reason a cancelled research request ended, read from the control journal for any reader.

A general operation reader (operation/result/read) must not know the research journal. It is given
this object and asks it, for an operation that ended CANCELLED with no stored error, whether the
person stopped the request. Every other operation gets no answer, so its reply stays as it was.
"""

from pydantic import JsonValue

from thoth.domain.enums import OperationState
from thoth.domain.operation import OperationRecord
from thoth.domain.research_stop import (
    RESEARCH_EXECUTION_NAMESPACE,
    ResearchStopNote,
    stop_note_key,
    user_stopped_error,
)
from thoth.ports.control_record import ControlRecordStorePort


class ResearchStopReasons:
    def __init__(self, controls: ControlRecordStorePort) -> None:
        self._controls = controls

    def error_for(self, operation: OperationRecord) -> dict[str, JsonValue] | None:
        if operation.state != OperationState.CANCELLED or operation.error is not None:
            return None
        record = self._controls.read(
            operation.project_id,
            RESEARCH_EXECUTION_NAMESPACE,
            stop_note_key(operation.operation_id),
        )
        if record is None:
            return None
        return user_stopped_error(ResearchStopNote.model_validate(record.payload))
