"""A project's choice about whether recalled memory is given to an investigation."""

from __future__ import annotations

from typing import Literal

from thoth.domain.base import DomainModel


class ProjectMemorySettings(DomainModel):
    record_kind: Literal["ProjectMemorySettings"] = "ProjectMemorySettings"
    schema_version: Literal["2.0.0"] = "2.0.0"
    project_id: str
    memory_injection: bool = True
