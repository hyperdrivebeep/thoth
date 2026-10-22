"""Typed MEMORY record creation; callers own transaction and execution order."""

from thoth.application.commands.memory_inputs import ProjectInput
from thoth.application.services.control_record_service import ControlRecordService
from thoth.domain.control_record import ControlRecord


def memory_record(
    controls: ControlRecordService,
    request: ProjectInput,
    kind: str,
    state: str,
    payload: dict[str, object],
    *,
    record_id: str | None = None,
) -> ControlRecord:
    return controls.create(
        project_id=request.project_id,
        namespace="MEMORY",
        record_type=kind,
        state=state,
        payload=payload,
        record_id=record_id,
    )
