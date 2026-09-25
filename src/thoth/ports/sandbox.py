from __future__ import annotations

from pathlib import Path
from typing import Protocol

from thoth.domain.sandbox import SandboxResult, SandboxRunSpec
from thoth.domain.sandbox_capability import SandboxCapability


class SandboxPort(Protocol):
    @property
    def capability(self) -> SandboxCapability: ...

    async def run(self, spec: SandboxRunSpec) -> SandboxResult: ...

    async def cancel(self, attempt_id: str) -> bool: ...


class SandboxFactoryRegistryPort(Protocol):
    def create(self, adapter_id: str, workspace: Path) -> SandboxPort: ...

    def capability(self, adapter_id: str) -> SandboxCapability: ...
