from __future__ import annotations

from thoth.domain.base import DomainModel
from thoth.domain.enums import ActorKind
from thoth.domain.ids import Identifier


class ActorRef(DomainModel):
    actor_id: Identifier
    kind: ActorKind
    role: str
    model_id: str | None = None
    tool_version: str | None = None
    project_id: str | None = None
    session_id: str | None = None
    role_assignment_ref: str | None = None
