from __future__ import annotations

import asyncio
import hashlib
import shlex
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast

from thoth.domain.sandbox import (
    SandboxExecutionState,
    SandboxNetworkPolicy,
    SandboxResult,
    SandboxRunSpec,
    SandboxRuntimeProfile,
)
from thoth.domain.sandbox_capability import SandboxCapability


class ManagedCommandResult(Protocol):
    stdout: str
    stderr: str
    exit_code: int
    error: str | None


class ManagedCommands(Protocol):
    def run(self, command: str, *, timeout: float | None = 60) -> ManagedCommandResult: ...


class ManagedFiles(Protocol):
    def write(self, path: str, data: bytes) -> object: ...


class ManagedSandbox(Protocol):
    @property
    def commands(self) -> ManagedCommands: ...

    def kill(self) -> bool: ...


class ManagedFileSandbox(Protocol):
    @property
    def files(self) -> ManagedFiles: ...


ManagedSandboxFactory = Callable[..., ManagedSandbox]


class E2BManagedSandboxAdapter:
    """Managed SandboxPort adapter using E2B with public internet disabled."""

    def __init__(
        self,
        *,
        template: str = "base",
        factory: ManagedSandboxFactory | None = None,
    ) -> None:
        self._template = template
        self._capability = SandboxCapability(
            runtime_profile=SandboxRuntimeProfile.MANAGED,
            available=True,
            runtime_version=f"e2b-template:{template}",
            security_tier="MANAGED_EPHEMERAL",
            network_modes=("DENY_ALL",),
        )
        self._factory = factory
        self._active: dict[str, ManagedSandbox] = {}
        self._lock = threading.Lock()

    @property
    def image_digest(self) -> str:
        return f"e2b-template:{self._template}"

    @property
    def capability(self) -> SandboxCapability:
        return self._capability

    @property
    def active_sandbox_count(self) -> int:
        with self._lock:
            return len(self._active)

    async def run(self, spec: SandboxRunSpec) -> SandboxResult:
        return await asyncio.to_thread(self._run_sync, spec)

    async def cancel(self, attempt_id: str) -> bool:
        with self._lock:
            sandbox = self._active.get(attempt_id)
        return False if sandbox is None else await asyncio.to_thread(sandbox.kill)

    def _create(self, spec: SandboxRunSpec) -> ManagedSandbox:
        if self._factory is not None:
            return self._factory(
                template=self._template,
                timeout=spec.resource_limits.wall_seconds + 30,
                metadata={"thoth_attempt": spec.attempt_id},
                secure=True,
                allow_internet_access=False,
            )
        from e2b import Sandbox

        return cast(
            ManagedSandbox,
            Sandbox.create(
                template=self._template,
                timeout=spec.resource_limits.wall_seconds + 30,
                metadata={"thoth_attempt": spec.attempt_id},
                secure=True,
                allow_internet_access=False,
            ),
        )

    def _run_sync(self, spec: SandboxRunSpec) -> SandboxResult:
        self._validate_spec(spec)
        started = datetime.now(UTC)
        sandbox: ManagedSandbox | None = None
        state = SandboxExecutionState.BOOT_FAILED
        exit_code: int | None = None
        stdout = ""
        stderr = ""
        failure: str | None = None
        cleanup = SandboxExecutionState.CLEANUP_FAILED
        try:
            sandbox = self._create(spec)
            with self._lock:
                self._active[spec.attempt_id] = sandbox
            self._stage_inputs(sandbox, spec)
            result = sandbox.commands.run(
                shlex.join(spec.argv),
                timeout=spec.resource_limits.wall_seconds,
            )
            exit_code = result.exit_code
            stdout, stdout_truncated = _truncate(result.stdout, spec.resource_limits.stdout_bytes)
            stderr, stderr_truncated = _truncate(result.stderr, spec.resource_limits.stderr_bytes)
            if stdout_truncated or stderr_truncated:
                state = SandboxExecutionState.OUTPUT_TRUNCATED
                failure = "managed sandbox output limit exceeded"
            else:
                state = (
                    SandboxExecutionState.SUCCEEDED
                    if exit_code == 0
                    else SandboxExecutionState.FAILED
                )
                failure = result.error
        except TimeoutError:
            state = SandboxExecutionState.TIMED_OUT
            failure = "managed sandbox wall-clock limit exceeded"
        except Exception as exc:
            state = SandboxExecutionState.BOOT_FAILED
            failure = type(exc).__name__
        finally:
            with self._lock:
                self._active.pop(spec.attempt_id, None)
            if sandbox is not None:
                try:
                    sandbox.kill()
                    cleanup = SandboxExecutionState.DESTROYED
                except Exception:
                    cleanup = SandboxExecutionState.CLEANUP_FAILED
            else:
                cleanup = SandboxExecutionState.DESTROYED
        return SandboxResult(
            project_id=spec.project_id,
            attempt_id=spec.attempt_id,
            runtime_profile=spec.runtime_profile,
            state=state,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            output_digests=(),
            started_at=started,
            completed_at=datetime.now(UTC),
            cleanup_state=cleanup,
            runtime_version="e2b-managed-v1",
            security_tier="S2_MANAGED_EPHEMERAL",
            failure_detail=failure,
        )

    def _validate_spec(self, spec: SandboxRunSpec) -> None:
        if spec.runtime_profile != SandboxRuntimeProfile.MANAGED:
            raise ValueError("E2B adapter requires MANAGED profile")
        if spec.image_digest != self.image_digest:
            raise ValueError("E2B template identity mismatch")
        if spec.network_policy != SandboxNetworkPolicy.DENY_ALL:
            raise ValueError("E2B v1 adapter requires public internet disabled")
        if spec.secret_refs:
            raise ValueError("E2B v1 adapter accepts no secrets")

    @staticmethod
    def _stage_inputs(sandbox: ManagedSandbox, spec: SandboxRunSpec) -> None:
        if not spec.input_snapshots:
            return
        files = cast(ManagedFileSandbox, sandbox).files
        for snapshot in spec.input_snapshots:
            raw = Path(snapshot.source_path).read_bytes()
            if hashlib.sha256(raw).hexdigest() != snapshot.content_sha256:
                raise ValueError("sandbox input digest mismatch")
            files.write(f"/inputs/{snapshot.target_name}", raw)


def _truncate(value: str, limit: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value, False
    return encoded[:limit].decode("utf-8", errors="ignore"), True
