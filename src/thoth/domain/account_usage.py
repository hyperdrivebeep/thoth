from typing import Literal

from pydantic import Field

from thoth.domain.base import DomainModel


class AccountQuotaSnapshot(DomainModel):
    record_kind: Literal["AccountQuotaSnapshot"] = "AccountQuotaSnapshot"
    schema_version: Literal["1.0.0"] = "1.0.0"
    state: Literal["UNKNOWN", "UNSUPPORTED", "OBSERVED", "STALE"] = "UNKNOWN"
    remaining_percent: float | None = Field(default=None, ge=0, le=100)
    window_minutes: int | None = Field(default=None, ge=1)
    resets_at: str | None = None
    observed_at: str | None = None
    source: Literal["CODEX_APP_SERVER_RATE_LIMITS", "UNAVAILABLE"] = "UNAVAILABLE"
    reason: str | None = Field(default=None, min_length=1, max_length=64)
    provider: str | None = Field(default=None, min_length=1, max_length=160)
