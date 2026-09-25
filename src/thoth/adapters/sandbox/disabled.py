from __future__ import annotations

from datetime import UTC, datetime

from thoth.domain.sandbox import (
    SandboxExecutionState,
    SandboxResult,
    SandboxRunSpec,
    SandboxRuntimeProfile,
)
from thoth.domain.sandbox_capability import SandboxCapability


class DisabledSandboxAdapter:
    @property
    def capability(self) -> SandboxCapability:
        return SandboxCapability(
            runtime_profile=SandboxRuntimeProfile.SCRIPTED,
            available=False,
            runtime_version="disabled:1",
            security_tier="UNAVAILABLE",
            network_modes=("DENY_ALL",),
        )

    async def run(self, spec: SandboxRunSpec) -> SandboxResult:
        now = datetime.now(UTC)
        return SandboxResult(
            project_id=spec.project_id,
            attempt_id=spec.attempt_id,
            runtime_profile=spec.runtime_profile,
            state=SandboxExecutionState.POLICY_DENIED,
            started_at=now,
            completed_at=now,
            cleanup_state=SandboxExecutionState.DESTROYED,
            failure_detail="sandbox adapter is not activated",
        )

    async def cancel(self, attempt_id: str) -> bool:
        del attempt_id
        return False
