from __future__ import annotations

from pydantic import AwareDatetime, model_validator

from thoth.domain.base import DomainModel


class TimeRange(DomainModel):
    start: AwareDatetime
    end: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_order(self) -> TimeRange:
        if self.end is not None and self.end < self.start:
            raise ValueError("end must be greater than or equal to start")
        return self
