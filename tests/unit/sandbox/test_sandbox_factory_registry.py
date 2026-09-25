from __future__ import annotations

from pathlib import Path

from thoth.adapters.sandbox import (
    E2BManagedSandboxAdapter,
    SandboxFactoryRegistration,
    SandboxFactoryRegistry,
    ScriptedSandboxAdapter,
    default_sandbox_factory_registry,
)
from thoth.domain.sandbox import SandboxRuntimeProfile
from thoth.domain.sandbox_capability import SandboxCapability


def test_sandbox_factory_registry_accepts_extension_without_profile_branch(
    tmp_path: Path,
) -> None:
    registry = SandboxFactoryRegistry()
    capability = SandboxCapability(
        runtime_profile=SandboxRuntimeProfile.SCRIPTED,
        available=True,
        runtime_version="scripted:custom-r2",
        security_tier="TEST_ONLY",
        network_modes=("DENY_ALL",),
    )
    adapter = ScriptedSandboxAdapter(capability=capability)
    registry.register(
        SandboxFactoryRegistration(
            adapter_id="custom-r2",
            capability=capability,
            factory=lambda workspace: adapter,
        )
    )

    assert registry.create("custom-r2", tmp_path) is adapter


def test_default_registry_materializes_managed_e2b_without_core_branch(
    tmp_path: Path,
) -> None:
    adapter = default_sandbox_factory_registry().create("managed-e2b", tmp_path)

    assert isinstance(adapter, E2BManagedSandboxAdapter)
