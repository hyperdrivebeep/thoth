from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from thoth.adapters.sandbox.disabled import DisabledSandboxAdapter
from thoth.adapters.sandbox.docker import DockerSandboxAdapter
from thoth.adapters.sandbox.e2b import E2BManagedSandboxAdapter
from thoth.adapters.sandbox.gvisor import GVisorSandboxAdapter
from thoth.domain.sandbox import SandboxRuntimeProfile
from thoth.domain.sandbox_capability import SandboxCapability
from thoth.ports.sandbox import SandboxFactoryRegistryPort, SandboxPort

SandboxFactory = Callable[[Path], SandboxPort]


@dataclass(frozen=True)
class SandboxFactoryRegistration:
    adapter_id: str
    capability: SandboxCapability
    factory: SandboxFactory


class SandboxFactoryRegistry:
    def __init__(self) -> None:
        self._registrations: dict[str, SandboxFactoryRegistration] = {}

    def register(self, registration: SandboxFactoryRegistration) -> None:
        normalized = registration.adapter_id.strip().lower()
        if not normalized or normalized in self._registrations:
            raise ValueError(
                f"sandbox factory already registered or invalid: {registration.adapter_id}"
            )
        self._registrations[normalized] = registration

    def create(self, adapter_id: str, workspace: Path) -> SandboxPort:
        normalized = adapter_id.strip().lower()
        try:
            registration = self._registrations[normalized]
        except KeyError as exc:
            raise ValueError(f"sandbox adapter is not registered: {adapter_id}") from exc
        adapter = registration.factory(workspace)
        if adapter.capability != registration.capability:
            raise ValueError(f"sandbox factory capability mismatch: {adapter_id}")
        return adapter

    def capability(self, adapter_id: str) -> SandboxCapability:
        normalized = adapter_id.strip().lower()
        try:
            return self._registrations[normalized].capability
        except KeyError as exc:
            raise ValueError(f"sandbox adapter is not registered: {adapter_id}") from exc


def default_sandbox_factory_registry() -> SandboxFactoryRegistryPort:
    registry = SandboxFactoryRegistry()
    registry.register(
        SandboxFactoryRegistration(
            adapter_id="disabled",
            capability=DisabledSandboxAdapter().capability,
            factory=lambda workspace: DisabledSandboxAdapter(),
        )
    )
    registry.register(
        SandboxFactoryRegistration(
            adapter_id="docker-poc",
            capability=SandboxCapability(
                runtime_profile=SandboxRuntimeProfile.DOCKER_POC,
                available=True,
                runtime_version="docker-cli:1",
                security_tier="POC_CONTAINER",
                network_modes=("DENY_ALL",),
            ),
            factory=lambda workspace: DockerSandboxAdapter(workspace / "sandbox" / "runs"),
        )
    )
    registry.register(
        SandboxFactoryRegistration(
            adapter_id="gvisor",
            capability=SandboxCapability(
                runtime_profile=SandboxRuntimeProfile.GVISOR,
                available=True,
                runtime_version="runsc:1",
                security_tier="USERSPACE_KERNEL",
                network_modes=("DENY_ALL",),
            ),
            factory=lambda workspace: GVisorSandboxAdapter(workspace / "sandbox" / "runs"),
        )
    )
    registry.register(
        SandboxFactoryRegistration(
            adapter_id="managed-e2b",
            capability=SandboxCapability(
                runtime_profile=SandboxRuntimeProfile.MANAGED,
                available=True,
                runtime_version="e2b-template:base",
                security_tier="MANAGED_EPHEMERAL",
                network_modes=("DENY_ALL",),
            ),
            factory=lambda workspace: E2BManagedSandboxAdapter(),
        )
    )
    return registry
