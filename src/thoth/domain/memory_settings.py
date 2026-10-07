"""A project's choice about whether recalled memory is given to an investigation."""

from __future__ import annotations

from typing import Literal

from thoth.domain.base import DomainModel

# Whether an investigation asks the model once for extra search words (see memory_expansion.py).
# One place to change the default if a measured comparison shows widening does not help.
QUERY_EXPANSION_DEFAULT_ON = True


class ProjectMemorySettings(DomainModel):
    record_kind: Literal["ProjectMemorySettings"] = "ProjectMemorySettings"
    schema_version: Literal["2.0.0"] = "2.0.0"
    project_id: str
    memory_injection: bool = True
    query_expansion: bool = QUERY_EXPANSION_DEFAULT_ON
