from __future__ import annotations

from pydantic import Field

from thoth.domain.base import DomainModel
from thoth.domain.sandbox import SandboxRuntimeProfile


class SandboxCapability(DomainModel):
    runtime_profile: SandboxRuntimeProfile
    available: bool
    runtime_version: str = Field(min_length=1, max_length=160)
    security_tier: str
    network_modes: tuple[str, ...]
    cleanup_contract: str = "DESTROYED_OR_TYPED_CLEANUP_FAILURE"
