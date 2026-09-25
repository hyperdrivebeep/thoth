from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import subprocess
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from thoth.domain.sandbox import (
    SandboxExecutionState,
    SandboxResult,
    SandboxRunSpec,
    SandboxRuntimeProfile,
)
from thoth.domain.sandbox_capability import SandboxCapability


class DockerSandboxAdapter:
    """POC-only Docker adapter. It never represents production accreditation."""

    def __init__(
        self,
        run_root: Path,
        *,
        docker_binary: str = "docker",
        runtime_name: str | None = None,
    ) -> None:
        self._run_root = run_root.resolve()
        self._run_root.mkdir(parents=True, exist_ok=True)
        self._docker = docker_binary
        self._runtime_name = runtime_name
        self._capability = SandboxCapability(
            runtime_profile=SandboxRuntimeProfile.DOCKER_POC,
            available=True,
            runtime_version="docker-cli:1",
            security_tier="POC_CONTAINER",
            network_modes=("DENY_ALL",),
        )
        self._active: dict[str, str] = {}
        self._lock = threading.Lock()

    @property
    def runtime_name(self) -> str | None:
        return self._runtime_name

    @property
    def capability(self) -> SandboxCapability:
        return self._capability

    async def run(self, spec: SandboxRunSpec) -> SandboxResult:
        return await asyncio.to_thread(self._run_sync, spec)

    async def cancel(self, attempt_id: str) -> bool:
        with self._lock:
            container_id = self._active.get(attempt_id)
        if container_id is None:
            return False
        await asyncio.to_thread(self._docker_call, "kill", container_id, timeout=15)
        return True

    def _run_sync(self, spec: SandboxRunSpec) -> SandboxResult:
        started = datetime.now(UTC)
        safe_attempt = "".join(c if c.isalnum() else "-" for c in spec.attempt_id)[-40:]
        container_name = f"thoth-{safe_attempt}-{uuid.uuid4().hex[:8]}".lower()
        run_dir = (self._run_root / container_name).resolve()
        if not run_dir.is_relative_to(self._run_root):
            raise ValueError("sandbox run directory escaped root")
        inputs_dir = run_dir / "inputs"
        outputs_dir = run_dir / "outputs"
        inputs_dir.mkdir(parents=True)
        outputs_dir.mkdir()
        state = SandboxExecutionState.BOOT_FAILED
        exit_code: int | None = None
        stdout = ""
        stderr = ""
        failure: str | None = None
        cleanup = SandboxExecutionState.CLEANUP_FAILED
        container_id: str | None = None
        output_digests: tuple[str, ...] = ()
        attach_stdout_truncated = False
        attach_stderr_truncated = False
        try:
            for snapshot in spec.input_snapshots:
                source = Path(snapshot.source_path).resolve()
                raw = source.read_bytes()
                if hashlib.sha256(raw).hexdigest() != snapshot.content_sha256:
                    raise ValueError("sandbox input digest mismatch")
                (inputs_dir / snapshot.target_name).write_bytes(raw)
            limits = spec.resource_limits
            create_args = [
                "create",
                "--name",
                container_name,
                "--network",
                "none",
                "--read-only",
                "--user",
                "65534:65534",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--pids-limit",
                str(limits.pids),
                "--memory",
                f"{limits.memory_mib}m",
                "--memory-swap",
                f"{limits.memory_mib}m",
                "--cpus",
                f"{limits.cpu_millis / 1000:.3f}",
                "--ulimit",
                "nofile=1024:1024",
                "--tmpfs",
                f"/tmp:rw,noexec,nosuid,nodev,size={min(limits.disk_mib, 64)}m",
                "--tmpfs",
                f"/work:rw,nosuid,nodev,size={min(limits.disk_mib, 128)}m",
                "--mount",
                f"type=bind,src={inputs_dir},dst=/inputs,readonly",
                "--mount",
                f"type=bind,src={outputs_dir},dst=/outputs",
                "--workdir",
                spec.working_directory,
            ]
            if self._runtime_name is not None:
                create_args.extend(("--runtime", self._runtime_name))
            create_args.extend((spec.image_digest, *spec.argv))
            created = self._docker_call(*create_args, timeout=60)
            container_id = created.stdout.strip()
            if not container_id:
                raise RuntimeError("docker create returned no container id")
            with self._lock:
                self._active[spec.attempt_id] = container_id
            try:
                (
                    completed,
                    attach_stdout_truncated,
                    attach_stderr_truncated,
                ) = self._docker_call_bounded(
                    "start",
                    "--attach",
                    container_id,
                    timeout=limits.wall_seconds,
                    stdout_limit=limits.stdout_bytes,
                    stderr_limit=limits.stderr_bytes,
                )
                if attach_stdout_truncated or attach_stderr_truncated:
                    self._docker_call("kill", container_id, timeout=15, check=False)
                exit_code = self._inspect_exit_code(container_id)
                stdout = completed.stdout
                stderr = completed.stderr
                state = (
                    SandboxExecutionState.SUCCEEDED
                    if exit_code == 0
                    else SandboxExecutionState.OOM_KILLED
                    if self._inspect_oom(container_id)
                    else SandboxExecutionState.FAILED
                )
            except subprocess.TimeoutExpired:
                self._docker_call("kill", container_id, timeout=15, check=False)
                state = SandboxExecutionState.TIMED_OUT
                failure = "wall-clock limit exceeded"
            stdout_truncated = attach_stdout_truncated
            stderr_truncated = attach_stderr_truncated
            if stdout_truncated or stderr_truncated:
                state = SandboxExecutionState.OUTPUT_TRUNCATED
                failure = "output limit exceeded"
            try:
                output_digests = _hash_outputs(outputs_dir, limits.disk_mib * 1024 * 1024)
            except ValueError:
                state = SandboxExecutionState.OUTPUT_TRUNCATED
                failure = "output disk limit or unsafe output entry"
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            failure = type(exc).__name__
            state = SandboxExecutionState.BOOT_FAILED
        finally:
            if container_id is not None:
                self._docker_call("rm", "--force", container_id, timeout=30, check=False)
            with self._lock:
                self._active.pop(spec.attempt_id, None)
            try:
                shutil.rmtree(run_dir)
                cleanup = SandboxExecutionState.DESTROYED
            except OSError:
                cleanup = SandboxExecutionState.CLEANUP_FAILED
        return SandboxResult(
            project_id=spec.project_id,
            attempt_id=spec.attempt_id,
            runtime_profile=spec.runtime_profile,
            state=state,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            output_digests=output_digests,
            started_at=started,
            completed_at=datetime.now(UTC),
            cleanup_state=cleanup,
            failure_detail=failure,
        )

    def _docker_call(
        self, *args: str, timeout: int, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [self._docker, *args],
            check=check,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={"PATH": os.environ.get("PATH", "")},
        )

    def _docker_call_bounded(
        self,
        *args: str,
        timeout: int,
        stdout_limit: int,
        stderr_limit: int,
    ) -> tuple[subprocess.CompletedProcess[str], bool, bool]:
        command = [self._docker, *args]
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            env={"PATH": os.environ.get("PATH", "")},
        )
        assert process.stdout is not None
        assert process.stderr is not None
        stdout = bytearray()
        stderr = bytearray()
        truncated = [False, False]
        overflow = threading.Event()
        readers = (
            threading.Thread(
                target=_drain_bounded,
                args=(process.stdout, stdout, stdout_limit, truncated, 0, overflow),
                daemon=True,
            ),
            threading.Thread(
                target=_drain_bounded,
                args=(process.stderr, stderr, stderr_limit, truncated, 1, overflow),
                daemon=True,
            ),
        )
        for reader in readers:
            reader.start()
        deadline = time.monotonic() + timeout
        timed_out = False
        while process.poll() is None:
            if overflow.wait(0.01):
                process.kill()
                break
            if time.monotonic() >= deadline:
                timed_out = True
                process.kill()
                break
        process.wait(timeout=5)
        for reader in readers:
            reader.join(timeout=5)
        if timed_out:
            raise subprocess.TimeoutExpired(command, timeout)
        return (
            subprocess.CompletedProcess(
                command,
                process.returncode,
                stdout.decode(errors="replace"),
                stderr.decode(errors="replace"),
            ),
            truncated[0],
            truncated[1],
        )

    def _inspect_exit_code(self, container_id: str) -> int:
        result = self._docker_call(
            "inspect", "--format", "{{.State.ExitCode}}", container_id, timeout=15
        )
        return int(result.stdout.strip())

    def _inspect_oom(self, container_id: str) -> bool:
        result = self._docker_call(
            "inspect", "--format", "{{.State.OOMKilled}}", container_id, timeout=15
        )
        return result.stdout.strip().lower() == "true"


def _drain_bounded(
    stream: BinaryIO,
    target: bytearray,
    limit: int,
    truncated: list[bool],
    index: int,
    overflow: threading.Event,
) -> None:
    while chunk := stream.read(64 * 1024):
        remaining = max(0, limit - len(target))
        target.extend(chunk[:remaining])
        if len(chunk) > remaining:
            truncated[index] = True
            overflow.set()


def _hash_outputs(root: Path, byte_limit: int) -> tuple[str, ...]:
    total = 0
    values: list[str] = []
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        if path.is_symlink() or not path.is_file():
            raise ValueError("sandbox output contains unsafe filesystem entry")
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("sandbox output escaped output root")
        raw = path.read_bytes()
        total += len(raw)
        if total > byte_limit:
            raise ValueError("sandbox output disk limit exceeded")
        values.append(hashlib.sha256(raw).hexdigest())
    return tuple(values)
