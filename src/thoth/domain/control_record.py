from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, Field

from thoth.domain.base import DomainModel
from thoth.domain.ids import ProjectId, Sha256


class ControlRecord(DomainModel):
    control_revision_id: str
    project_id: ProjectId
    namespace: str
    record_type: str
    record_id: str
    version: int = Field(ge=1)
    state: str
    payload: dict[str, object]
    record_digest: Sha256
    supersedes_digest: Sha256 | None = None
    created_at: AwareDatetime
    canonical_truth: Literal[False] = False
    schema_version: str = "1.0.0"
