from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class DomainModel(BaseModel):
    """Immutable, strict base for canonical domain payloads."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)
