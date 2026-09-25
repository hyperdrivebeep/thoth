from __future__ import annotations

from pathlib import Path

import pytest

from thoth.adapters.sandbox import (
    SandboxFactoryRegistration,
    SandboxFactoryRegistry,
    ScriptedSandboxAdapter,
)
from thoth.domain.sandbox import SandboxRuntimeProfile
from thoth.domain.sandbox_capability import SandboxCapability


def capability(profile: SandboxRuntimeProfile, version: str) -> SandboxCapability:
    return SandboxCapability(
        runtime_profile=profile,
        available=True,
        runtime_version=version,
        security_tier="TEST_ONLY",
        network_modes=("DENY_ALL",),
    )


def test_registry_adds_unfamiliar_adapter_from_one_registration(tmp_path: Path) -> None:
    registry = SandboxFactoryRegistry()
    expected = capability(SandboxRuntimeProfile.SCRIPTED, "unfamiliar:1")
    registry.register(
        SandboxFactoryRegistration(
            adapter_id="unfamiliar-r2",
            capability=expected,
            factory=lambda workspace: ScriptedSandboxAdapter(capability=expected),
        )
    )

    adapter = registry.create("unfamiliar-r2", tmp_path)

    assert adapter.capability == expected


def test_registry_rejects_factory_capability_mismatch(tmp_path: Path) -> None:
    registry = SandboxFactoryRegistry()
    declared = capability(SandboxRuntimeProfile.DOCKER_POC, "docker:declared")
    actual = capability(SandboxRuntimeProfile.SCRIPTED, "scripted:actual")
    registry.register(
        SandboxFactoryRegistration(
            adapter_id="mismatch",
            capability=declared,
            factory=lambda workspace: ScriptedSandboxAdapter(capability=actual),
        )
    )

    with pytest.raises(ValueError, match="capability mismatch"):
        registry.create("mismatch", tmp_path)
