from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from thoth.domain.sandbox import (
    SandboxExecutionState,
    SandboxNetworkPolicy,
    SandboxResult,
    SandboxRunSpec,
    SandboxRuntimeProfile,
)
from thoth.domain.sandbox_capability import SandboxCapability


class FirecrackerSandboxAdapter:
    """Fixed-bundle Firecracker boot adapter for a networkless R2 microVM fixture."""

    def __init__(
        self,
        *,
        firecracker_binary: Path,
        config_path: Path,
        run_root: Path,
        expected_marker: str,
    ) -> None:
        self._binary = firecracker_binary.resolve()
        self._config = config_path.resolve()
        self._run_root = run_root.resolve()
        self._marker = expected_marker
        self._run_root.mkdir(parents=True, exist_ok=True)
        self._assets = self._validate_config()
        self._bundle_digest = self._calculate_bundle_digest()
        self._capability = SandboxCapability(
            runtime_profile=SandboxRuntimeProfile.FIRECRACKER,
            available=True,
            runtime_version=f"firecracker-bundle:{self._bundle_digest[:16]}",
            security_tier="MICROVM",
            network_modes=("DENY_ALL",),
        )
        self._active: dict[str, subprocess.Popen[str]] = {}
        self._lock = threading.Lock()

    @property
    def bundle_digest(self) -> str:
        return self._bundle_digest

    @property
    def capability(self) -> SandboxCapability:
        return self._capability

    @property
    def image_digest(self) -> str:
        return f"firecracker-bundle@sha256:{self._bundle_digest}"

    async def run(self, spec: SandboxRunSpec) -> SandboxResult:
        import asyncio

        return await asyncio.to_thread(self._run_sync, spec)

    async def cancel(self, attempt_id: str) -> bool:
        with self._lock:
            process = self._active.get(attempt_id)
        if process is None or process.poll() is not None:
            return False
        process.terminate()
        return True

    def _run_sync(self, spec: SandboxRunSpec) -> SandboxResult:
        self._validate_spec(spec)
        started = datetime.now(UTC)
        run_dir = self._run_root / _safe_segment(spec.attempt_id)
        if not run_dir.resolve().is_relative_to(self._run_root):
            raise ValueError("Firecracker run path escaped root")
        run_dir.mkdir(parents=True, exist_ok=False)
        output: list[str] = []
        marker_seen = threading.Event()
        process: subprocess.Popen[str] | None = None
        state = SandboxExecutionState.BOOT_FAILED
        failure: str | None = None
        cleanup = SandboxExecutionState.CLEANUP_FAILED
        try:
            process = subprocess.Popen(
                [
                    str(self._binary),
                    "--no-api",
                    "--config-file",
                    str(self._config),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env={"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin"},
            )
            with self._lock:
                self._active[spec.attempt_id] = process

            def read_console() -> None:
                assert process is not None
                assert process.stdout is not None
                for line in process.stdout:
                    output.append(line)
                    if self._marker in line:
                        marker_seen.set()

            reader = threading.Thread(target=read_console, daemon=True)
            reader.start()
            if marker_seen.wait(spec.resource_limits.wall_seconds):
                state = SandboxExecutionState.SUCCEEDED
            else:
                state = SandboxExecutionState.TIMED_OUT
                failure = "guest success marker was not observed"
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            reader.join(timeout=2)
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            state = SandboxExecutionState.BOOT_FAILED
            failure = type(exc).__name__
        finally:
            with self._lock:
                self._active.pop(spec.attempt_id, None)
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            try:
                shutil.rmtree(run_dir)
                cleanup = SandboxExecutionState.DESTROYED
            except OSError:
                cleanup = SandboxExecutionState.CLEANUP_FAILED
        stdout, truncated = _truncate("".join(output), spec.resource_limits.stdout_bytes)
        if truncated:
            state = SandboxExecutionState.OUTPUT_TRUNCATED
            failure = "serial output limit exceeded"
        return SandboxResult(
            project_id=spec.project_id,
            attempt_id=spec.attempt_id,
            runtime_profile=spec.runtime_profile,
            state=state,
            exit_code=0 if state == SandboxExecutionState.SUCCEEDED else None,
            stdout=stdout,
            stderr="",
            output_digests=(),
            started_at=started,
            completed_at=datetime.now(UTC),
            cleanup_state=cleanup,
            failure_detail=failure,
        )

    def _validate_spec(self, spec: SandboxRunSpec) -> None:
        if spec.runtime_profile != SandboxRuntimeProfile.FIRECRACKER:
            raise ValueError("Firecracker adapter requires FIRECRACKER profile")
        if spec.image_digest != self.image_digest:
            raise ValueError("Firecracker bundle digest mismatch")
        if spec.argv != ("boot",):
            raise ValueError("Firecracker v1 adapter only accepts the sealed boot fixture")
        if spec.input_snapshots or spec.secret_refs:
            raise ValueError("Firecracker v1 adapter accepts no external inputs or secrets")
        if spec.network_policy != SandboxNetworkPolicy.DENY_ALL:
            raise ValueError("Firecracker v1 adapter is networkless")

    def _validate_config(self) -> tuple[Path, ...]:
        if not self._binary.is_file() or not self._config.is_file():
            raise ValueError("Firecracker binary or config is missing")
        raw = cast(object, json.loads(self._config.read_text(encoding="utf-8")))
        if not isinstance(raw, dict):
            raise ValueError("Firecracker config must be an object")
        config = cast(dict[str, object], raw)
        network_interfaces = config.get("network-interfaces")
        if network_interfaces is not None and network_interfaces != []:
            raise ValueError("Firecracker v1 config must not include network interfaces")
        boot = config.get("boot-source")
        drives = config.get("drives")
        if not isinstance(boot, dict) or not isinstance(drives, list) or not drives:
            raise ValueError("Firecracker config requires boot source and root drive")
        boot_values = cast(dict[str, object], boot)
        kernel_value = boot_values.get("kernel_image_path")
        if not isinstance(kernel_value, str):
            raise ValueError("Firecracker kernel path is missing")
        assets = [self._binary, self._config, Path(kernel_value).resolve()]
        for drive_value in cast(list[object], drives):
            if not isinstance(drive_value, dict):
                raise ValueError("Firecracker drive config is invalid")
            drive = cast(dict[str, object], drive_value)
            path_value = drive.get("path_on_host")
            if not isinstance(path_value, str):
                raise ValueError("Firecracker drive path is missing")
            if drive.get("is_root_device") is True and drive.get("is_read_only") is not True:
                raise ValueError("Firecracker root drive must be read-only")
            assets.append(Path(path_value).resolve())
        if any(not item.is_file() for item in assets):
            raise ValueError("Firecracker bundle asset is missing")
        return tuple(assets)

    def _calculate_bundle_digest(self) -> str:
        digest = hashlib.sha256()
        for path in sorted(self._assets, key=lambda item: str(item)):
            digest.update(str(path).encode())
            digest.update(b"\0")
            with path.open("rb") as stream:
                while block := stream.read(1024 * 1024):
                    digest.update(block)
        return digest.hexdigest()


def _truncate(value: str, limit: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value, False
    return encoded[:limit].decode("utf-8", errors="ignore"), True


def _safe_segment(value: str) -> str:
    normalized = "".join(
        character if character.isalnum() or character in "-_." else "_" for character in value
    )
    return normalized[:160] or "attempt"
