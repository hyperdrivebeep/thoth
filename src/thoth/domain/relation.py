from __future__ import annotations

from pydantic import Field

from thoth.domain.base import DomainModel
from thoth.domain.ids import ProjectId, Sha256


class DependencyRelation(DomainModel):
    relation_id: str
    project_id: ProjectId
    source_ref: str
    relation_type: str
    target_ref: str
    payload: dict[str, object] = Field(default_factory=dict)
    revision_digest: Sha256
