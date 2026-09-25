from __future__ import annotations

from pydantic import AwareDatetime, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.enums import DecisionObjectLifecycle, ResolutionState
from thoth.domain.ids import DecisionObjectId, ProjectId, ThreadId


class DecisionObject(DomainModel):
    object_id: DecisionObjectId
    project_id: ProjectId
    thread_id: ThreadId
    title: str
    problem: str
    object_profile: str
    lifecycle: DecisionObjectLifecycle = DecisionObjectLifecycle.OPEN
    resolution_state: ResolutionState = ResolutionState.UNRESOLVED
    unresolved_refs: tuple[str, ...] = ()
    created_at: AwareDatetime
    schema_version: str = "1.0.0"

    @model_validator(mode="after")
    def closed_requires_resolution(self) -> DecisionObject:
        if self.lifecycle == DecisionObjectLifecycle.CLOSED and self.resolution_state not in {
            ResolutionState.RESOLVED,
            ResolutionState.ABSTAINED,
        }:
            raise ValueError("closed decision object requires resolved or abstained state")
        return self
