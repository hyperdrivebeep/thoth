from __future__ import annotations

from pydantic import Field

from thoth.domain.base import DomainModel


class ProjectPackDescriptor(DomainModel):
    pack_name: str
    pack_id: str
    project_id: str
    name: str
    problem: str
    scripted_allowed: bool
    default_provider: str


class ProjectPackExecutionRequest(DomainModel):
    provider: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9._-]+$")
    model: str | None = Field(default=None, max_length=160)
    scripted: bool = False
