from __future__ import annotations

from thoth.domain.sandbox import SandboxRuntimeProfile
from thoth.domain.sandbox_capability import SandboxCapability
from thoth.ports.sandbox import SandboxPort
from thoth.ports.sandbox_capability import SandboxCapabilityRouterPort


class RegisteredSandboxRouter(SandboxCapabilityRouterPort):
    def __init__(self) -> None:
        self._values: dict[SandboxRuntimeProfile, tuple[SandboxPort, SandboxCapability]] = {}

    def register(
        self,
        profile: SandboxRuntimeProfile,
        adapter: SandboxPort,
        *,
        available: bool,
        version: str,
        security_tier: str = "POLICY_BOUND",
        network_modes: tuple[str, ...] = ("DENY_ALL",),
    ) -> None:
        if profile in self._values:
            raise ValueError(f"sandbox runtime already registered: {profile.value}")
        self._values[profile] = (
            adapter,
            SandboxCapability(
                runtime_profile=profile,
                available=available,
                runtime_version=version,
                security_tier=security_tier,
                network_modes=network_modes,
            ),
        )

    def resolve(
        self, profile: SandboxRuntimeProfile
    ) -> tuple[SandboxPort, SandboxCapability]:
        try:
            adapter, capability = self._values[profile]
        except KeyError as exc:
            raise ValueError(f"sandbox runtime is not registered: {profile.value}") from exc
        if not capability.available:
            raise ValueError(f"sandbox runtime capability is unavailable: {profile.value}")
        return adapter, capability
