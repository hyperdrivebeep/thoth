from __future__ import annotations

from typing import Protocol

from thoth.domain.sandbox import SandboxRuntimeProfile
from thoth.domain.sandbox_capability import SandboxCapability
from thoth.ports.sandbox import SandboxPort


class SandboxCapabilityRouterPort(Protocol):
    def resolve(
        self, profile: SandboxRuntimeProfile
    ) -> tuple[SandboxPort, SandboxCapability]: ...
