from __future__ import annotations

from pydantic import AwareDatetime, Field

from thoth.domain.base import DomainModel
from thoth.domain.ids import CycleId, ProjectId, Sha256, ThreadId


class ThreadActivity(DomainModel):
    activity_id: str
    project_id: ProjectId
    thread_id: ThreadId
    cycle_id: CycleId
    event_type: str
    payload: dict[str, object]
    actor_id: str
    activity_digest: Sha256
    created_at: AwareDatetime
    schema_version: str = "1.0.0"


class ThreadCheckpoint(DomainModel):
    checkpoint_id: str
    project_id: ProjectId
    thread_id: ThreadId
    cycle_id: CycleId
    head_set_digest: Sha256
    payload: dict[str, object]
    checkpoint_digest: Sha256
    created_at: AwareDatetime
    schema_version: str = "1.0.0"


class ThreadInputRecord(DomainModel):
    input_id: str
    project_id: ProjectId
    thread_id: ThreadId
    kind: str
    text: str
    state: str = "QUEUED"
    ordinal: int = Field(ge=1)
    created_at: AwareDatetime
    schema_version: str = "1.0.0"
