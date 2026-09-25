from __future__ import annotations

from pydantic import AwareDatetime, Field

from thoth.domain.base import DomainModel
from thoth.domain.enums import ProjectLifecycle, ThreadExecutionState, ThreadLifecycle
from thoth.domain.ids import CycleId, DecisionObjectId, ProjectId, Sha256, ThreadId


class Project(DomainModel):
    project_id: ProjectId
    name: str
    description: str = ""
    cutoff_at: AwareDatetime
    lifecycle: ProjectLifecycle = ProjectLifecycle.DRAFT
    overlay: str
    policy_binding_ref: str
    source_binding_ids: tuple[str, ...] = ()
    revision: int = 0
    schema_version: str = "1.0.0"


class WorkThread(DomainModel):
    thread_id: ThreadId
    project_id: ProjectId
    cycle_id: CycleId
    problem: str
    display_name: str = ""
    scope: dict[str, str] = Field(default_factory=dict)
    parent_thread_id: ThreadId | None = None
    fork_origin: str | None = None
    lifecycle: ThreadLifecycle = ThreadLifecycle.OPEN
    execution_state: ThreadExecutionState = ThreadExecutionState.IDLE
    current_object_ids: tuple[DecisionObjectId, ...] = ()
    working_head_digest: Sha256
    revision: int = 0
    created_at: AwareDatetime | None = None
    updated_at: AwareDatetime | None = None
    schema_version: str = "1.0.0"
