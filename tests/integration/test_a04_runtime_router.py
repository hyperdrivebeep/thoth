from __future__ import annotations

from pathlib import Path

import pytest

from thoth.adapters.sandbox import ScriptedSandboxAdapter
from thoth.application.services.sandbox_router import RegisteredSandboxRouter
from thoth.domain.sandbox import SandboxRuntimeProfile


def test_runtime_router_selects_exact_profile_without_downgrade(tmp_path: Path) -> None:
    scripted = ScriptedSandboxAdapter()
    docker = ScriptedSandboxAdapter()
    router = RegisteredSandboxRouter()
    router.register(SandboxRuntimeProfile.SCRIPTED, scripted, available=True, version="scripted:1")
    router.register(SandboxRuntimeProfile.DOCKER_POC, docker, available=True, version="docker:1")
    selected, capability = router.resolve(SandboxRuntimeProfile.DOCKER_POC)
    assert selected is docker
    assert capability.runtime_profile == SandboxRuntimeProfile.DOCKER_POC
    assert capability.available is True
    assert capability.runtime_version == "docker:1"


def test_runtime_router_holds_when_exact_capability_is_unavailable() -> None:
    router = RegisteredSandboxRouter()
    router.register(
        SandboxRuntimeProfile.GVISOR,
        ScriptedSandboxAdapter(),
        available=False,
        version="runsc:not-found",
    )
    with pytest.raises(ValueError, match="unavailable"):
        router.resolve(SandboxRuntimeProfile.GVISOR)
