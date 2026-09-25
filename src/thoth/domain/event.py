from __future__ import annotations

from pydantic import AwareDatetime, Field

from thoth.domain.base import DomainModel
from thoth.domain.ids import CheckpointId, OperationId, ProjectId, Sha256


class EventRecord(DomainModel):
    event_id: str
    project_id: ProjectId
    operation_id: OperationId
    event_type: str
    payload: dict[str, object] = Field(default_factory=dict)
    previous_event_digest: Sha256 | None = None
    event_digest: Sha256
    created_at: AwareDatetime
    schema_version: str = "1.0.0"


class Checkpoint(DomainModel):
    checkpoint_id: CheckpointId
    operation_id: OperationId
    payload: dict[str, object]
    checkpoint_digest: Sha256
    created_at: AwareDatetime
    schema_version: str = "1.0.0"
