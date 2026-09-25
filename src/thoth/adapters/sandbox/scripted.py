from __future__ import annotations

from collections import deque
from datetime import UTC, datetime

from thoth.domain.sandbox import (
    SandboxExecutionState,
    SandboxResult,
    SandboxRunSpec,
    SandboxRuntimeProfile,
)
from thoth.domain.sandbox_capability import SandboxCapability


class ScriptedSandboxAdapter:
    def __init__(
        self,
        states: tuple[SandboxExecutionState, ...] = (),
        *,
        capability: SandboxCapability | None = None,
    ) -> None:
        self._states = deque(states)
        self._capability = capability or SandboxCapability(
            runtime_profile=SandboxRuntimeProfile.SCRIPTED,
            available=True,
            runtime_version="scripted:1",
            security_tier="TEST_ONLY",
            network_modes=("DENY_ALL",),
        )
        self.seen_specs: list[SandboxRunSpec] = []
        self.cancelled: set[str] = set()

    @property
    def capability(self) -> SandboxCapability:
        return self._capability

    async def run(self, spec: SandboxRunSpec) -> SandboxResult:
        self.seen_specs.append(spec)
        started = datetime.now(UTC)
        state = self._states.popleft() if self._states else SandboxExecutionState.SUCCEEDED
        if spec.attempt_id in self.cancelled:
            state = SandboxExecutionState.CANCELLED
        return SandboxResult(
            project_id=spec.project_id,
            attempt_id=spec.attempt_id,
            runtime_profile=spec.runtime_profile,
            state=state,
            exit_code=0 if state == SandboxExecutionState.SUCCEEDED else 1,
            stdout="scripted sandbox output" if state == SandboxExecutionState.SUCCEEDED else "",
            stderr="" if state == SandboxExecutionState.SUCCEEDED else state.value,
            started_at=started,
            completed_at=datetime.now(UTC),
            cleanup_state=SandboxExecutionState.DESTROYED,
            failure_detail=None if state == SandboxExecutionState.SUCCEEDED else state.value,
        )

    async def cancel(self, attempt_id: str) -> bool:
        self.cancelled.add(attempt_id)
        return True
