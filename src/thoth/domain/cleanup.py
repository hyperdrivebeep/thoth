"""Cleanup has its own allowance; it never admits new research I/O."""

from typing import Literal

from thoth.domain.base import DomainModel


class CleanupUsage(DomainModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    parent_operation_id: str | None
    connector_run_id: str
    connector_id: str
    cleanup_attempt_id: str
    elapsed_ms: int
    limit_ms: Literal[2000] = 2000
    state: Literal["PENDING", "COMPLETED", "CANCEL_REQUESTED", "FAILED", "UNKNOWN"]
    remote_stop: Literal["UNKNOWN"] = "UNKNOWN"
    observation_persisted: bool = True
