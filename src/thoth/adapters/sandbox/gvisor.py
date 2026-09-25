from __future__ import annotations

from pathlib import Path

from thoth.adapters.sandbox.docker import DockerSandboxAdapter
from thoth.domain.sandbox import SandboxRuntimeProfile
from thoth.domain.sandbox_capability import SandboxCapability


class GVisorSandboxAdapter(DockerSandboxAdapter):
    """Linux staging adapter using Docker/containerd's registered runsc runtime."""

    def __init__(
        self,
        run_root: Path,
        *,
        docker_binary: str = "docker",
        runtime_name: str = "runsc",
    ) -> None:
        super().__init__(
            run_root,
            docker_binary=docker_binary,
            runtime_name=runtime_name,
        )
        self._capability = SandboxCapability(
            runtime_profile=SandboxRuntimeProfile.GVISOR,
            available=True,
            runtime_version=f"{runtime_name}:1",
            security_tier="USERSPACE_KERNEL",
            network_modes=("DENY_ALL",),
        )
