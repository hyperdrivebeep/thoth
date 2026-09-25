from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from thoth.adapters.sandbox import (
    DockerSandboxAdapter,
    E2BManagedSandboxAdapter,
    GVisorSandboxAdapter,
    ScriptedSandboxAdapter,
)
from thoth.application.services.control_record_service import ControlRecordService
from thoth.application.services.ingestion_service import IngestionService
from thoth.application.services.policy_gate import PolicyGate
from thoth.application.services.sandbox_router import RegisteredSandboxRouter
from thoth.application.services.sandbox_service import SandboxService
from thoth.domain.artifact import ArtifactEnvelope
from thoth.domain.enums import AuthorityState, CutoffState, SecurityClass
from thoth.domain.governance import ProjectPolicy
from thoth.domain.project import Project
from thoth.domain.sandbox import (
    SandboxExecutionState,
    SandboxFailure,
    SandboxInputSnapshot,
    SandboxNetworkPolicy,
    SandboxRunSpec,
    SandboxRuntimeProfile,
)
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.ledger import LedgerPort
from thoth.ports.object_store import ObjectStorePort
from thoth.ports.project import ProjectStorePort
from thoth.ports.runtime import ClockPort, IdGeneratorPort


def spec(**updates: object) -> SandboxRunSpec:
    value: dict[str, object] = {
        "project_id": "project:sandbox",
        "attempt_id": "attempt:1",
        "runtime_profile": "SCRIPTED",
        "image_digest": "scripted:image",
        "argv": ["python", "-c", "print(1)"],
        "policy_id": "policy:sandbox:v1",
        "policy_revision": 1,
        "policy_digest": "a" * 64,
    }
    value.update(updates)
    return SandboxRunSpec.model_validate(value)


def test_run_spec_forbids_secrets_and_unbounded_docker_image() -> None:
    with pytest.raises(ValidationError):
        spec(secret_refs=["secret:db"])
    with pytest.raises(ValidationError):
        spec(runtime_profile="DOCKER_POC", image_digest="python:latest")
    with pytest.raises(ValidationError):
        spec(
            runtime_profile="DOCKER_POC",
            image_digest=(
                "--mount=type=bind,src=/,dst=/host@sha256:" + "a" * 64
            ),
            argv=[f"python@sha256:{'b' * 64}", "-c", "print(1)"],
        )
    with pytest.raises(ValidationError):
        spec(
            runtime_profile="GVISOR",
            image_digest=f"python@sha256:{'a' * 64} --privileged",
        )
    with pytest.raises(ValidationError):
        spec(
            network_policy=SandboxNetworkPolicy.ALLOWLIST,
            allowed_hosts=[],
        )


@pytest.mark.asyncio
async def test_scripted_adapter_exposes_typed_terminal_state() -> None:
    adapter = ScriptedSandboxAdapter((SandboxExecutionState.TIMED_OUT,))
    result = await adapter.run(spec())

    assert result.state == SandboxExecutionState.TIMED_OUT
    assert result.cleanup_state == SandboxExecutionState.DESTROYED
    assert result.runtime_profile == SandboxRuntimeProfile.SCRIPTED


class FakeDockerAdapter(DockerSandboxAdapter):
    def __init__(self, root: Path, *, timeout: bool = False, exit_code: int = 0) -> None:
        super().__init__(root)
        self.calls: list[tuple[str, ...]] = []
        self.timeout = timeout
        self.exit_code = exit_code

    def _docker_call(
        self, *args: str, timeout: int, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        del check
        self.calls.append(args)
        if args[0] == "create":
            return subprocess.CompletedProcess(args, 0, "container-1\n", "")
        if args[0] == "start":
            if self.timeout:
                raise subprocess.TimeoutExpired(args, timeout)
            return subprocess.CompletedProcess(
                args,
                self.exit_code,
                "bounded output\n",
                "failed\n" if self.exit_code else "",
            )
        if args[0] == "inspect" and "{{.State.ExitCode}}" in args:
            return subprocess.CompletedProcess(args, 0, f"{self.exit_code}\n", "")
        if args[0] == "inspect" and "{{.State.OOMKilled}}" in args:
            return subprocess.CompletedProcess(args, 0, "false\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    def _docker_call_bounded(
        self,
        *args: str,
        timeout: int,
        stdout_limit: int,
        stderr_limit: int,
    ) -> tuple[subprocess.CompletedProcess[str], bool, bool]:
        del stdout_limit, stderr_limit
        self.calls.append(args)
        if self.timeout:
            raise subprocess.TimeoutExpired(args, timeout)
        return (
            subprocess.CompletedProcess(
                args,
                self.exit_code,
                "bounded output\n",
                "failed\n" if self.exit_code else "",
            ),
            False,
            False,
        )


@pytest.mark.asyncio
async def test_docker_poc_builds_hardened_argv_and_cleans_up(tmp_path: Path) -> None:
    adapter = FakeDockerAdapter(tmp_path / "runs")
    docker_spec = spec(
        runtime_profile="DOCKER_POC",
        image_digest=f"python@sha256:{'a' * 64}",
    )

    result = await adapter.run(docker_spec)

    create = adapter.calls[0]
    assert result.state == SandboxExecutionState.SUCCEEDED
    assert result.cleanup_state == SandboxExecutionState.DESTROYED
    network_index = create.index("--network")
    assert create[network_index : network_index + 2] == ("--network", "none")
    assert "--read-only" in create
    assert create[create.index("--cap-drop") : create.index("--cap-drop") + 2] == (
        "--cap-drop",
        "ALL",
    )
    assert "no-new-privileges:true" in create
    assert any(call[0] == "rm" and "--force" in call for call in adapter.calls)


@pytest.mark.asyncio
async def test_docker_timeout_is_typed_and_killed(tmp_path: Path) -> None:
    adapter = FakeDockerAdapter(tmp_path / "runs", timeout=True)
    result = await adapter.run(
        spec(
            runtime_profile="DOCKER_POC",
            image_digest=f"python@sha256:{'b' * 64}",
        )
    )

    assert result.state == SandboxExecutionState.TIMED_OUT
    assert any(call[0] == "kill" for call in adapter.calls)


@pytest.mark.asyncio
async def test_nonzero_container_exit_is_failed_not_boot_failed(tmp_path: Path) -> None:
    adapter = FakeDockerAdapter(tmp_path / "runs", exit_code=7)
    result = await adapter.run(
        spec(
            runtime_profile="DOCKER_POC",
            image_digest=f"python@sha256:{'c' * 64}",
        )
    )

    assert result.state == SandboxExecutionState.FAILED
    assert result.exit_code == 7


def test_gvisor_adapter_declares_runsc_runtime(tmp_path: Path) -> None:
    adapter = GVisorSandboxAdapter(tmp_path / "runs")
    assert adapter.runtime_name == "runsc"


class FakeCommandResult:
    stdout = "THOTH_MANAGED_PASS\n"
    stderr = ""
    exit_code = 0
    error: str | None = None


class FakeManagedCommands:
    def __init__(self) -> None:
        self.command = ""

    def run(self, command: str, *, timeout: float | None = 60) -> FakeCommandResult:
        del timeout
        self.command = command
        return FakeCommandResult()


class FakeManagedSandbox:
    def __init__(self) -> None:
        self._commands = FakeManagedCommands()
        self.killed = False

    @property
    def commands(self) -> FakeManagedCommands:
        return self._commands

    def kill(self) -> bool:
        self.killed = True
        return True


@pytest.mark.asyncio
async def test_managed_adapter_disables_internet_and_cleans_up() -> None:
    created: dict[str, object] = {}
    sandbox = FakeManagedSandbox()

    def factory(**kwargs: object) -> FakeManagedSandbox:
        created.update(kwargs)
        return sandbox

    adapter = E2BManagedSandboxAdapter(factory=factory)
    managed_spec = spec(
        runtime_profile="MANAGED",
        image_digest="e2b-template:base",
        argv=("printf", "THOTH_MANAGED_PASS"),
    )

    result = await adapter.run(managed_spec)

    assert result.state == SandboxExecutionState.SUCCEEDED
    assert result.cleanup_state == SandboxExecutionState.DESTROYED
    assert created["allow_internet_access"] is False
    assert sandbox.killed is True
    assert sandbox.commands.command == "printf THOTH_MANAGED_PASS"


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 31, tzinfo=UTC)


class CrossProjectArtifacts:
    def read_artifact(self, artifact_id: str) -> ArtifactEnvelope | None:
        return ArtifactEnvelope(
            artifact_id=artifact_id,
            project_id="project:other",
            source_uri="file:///other/evidence.txt",
            media_type="text/plain",
            byte_sha256="c" * 64,
            authority=AuthorityState.OFFICIAL,
            cutoff_state=CutoffState.ELIGIBLE,
            security_class=SecurityClass.INTERNAL,
            retrieved_at=datetime(2026, 8, 31, tzinfo=UTC),
            parser_name="text",
            parser_version="1",
        )


class StaticSandboxProjectStore:
    def read(self, project_id: str) -> Project | None:
        return Project(
            project_id=project_id,
            name="Sandbox",
            cutoff_at=datetime(2026, 8, 31, tzinfo=UTC),
            overlay="default",
            policy_binding_ref="policy:sandbox:v1",
        )


class StaticSandboxPolicyStore:
    def read_policy(self, project_id: str) -> ProjectPolicy | None:
        return ProjectPolicy(
            policy_id="policy:sandbox:v1",
            project_id=project_id,
            version=1,
            payload={
                "connector_allowlist": [],
                "connector_allowed_egress_classes": ["NONE"],
                "max_source_security_class": "RESTRICTED",
                "sandbox_runtime_allowlist": ["SCRIPTED"],
                "sandbox_network_policy": "DENY_ALL",
                "sandbox_allowed_hosts": [],
            },
            policy_digest="a" * 64,
            created_at=datetime(2026, 8, 31, tzinfo=UTC),
        )


@pytest.mark.asyncio
async def test_sandbox_rejects_cross_project_input_before_adapter(tmp_path: Path) -> None:
    adapter = ScriptedSandboxAdapter()
    router = RegisteredSandboxRouter()
    router.register(
        SandboxRuntimeProfile.SCRIPTED,
        adapter,
        available=True,
        version="scripted:test",
    )
    service = SandboxService(
        router=router,
        artifacts=cast(ArtifactLedgerPort, CrossProjectArtifacts()),
        objects=cast(ObjectStorePort, object()),
        controls=cast(ControlRecordService, object()),
        ingestion=cast(IngestionService, object()),
        projects=cast(ProjectStorePort, StaticSandboxProjectStore()),
        policies=PolicyGate(
            policies=StaticSandboxPolicyStore(),
            clock=FixedClock(),
        ),
        ledger=cast(LedgerPort, object()),
        clock=cast(ClockPort, FixedClock()),
        ids=cast(IdGeneratorPort, object()),
    )
    cross_project_spec = spec(
        input_snapshots=(
            SandboxInputSnapshot(
                artifact_id="artifact:other",
                content_sha256="c" * 64,
                source_path=str(tmp_path / "not-read.txt"),
                target_name="evidence.txt",
            ),
        )
    )

    with pytest.raises(SandboxFailure, match="outside the project"):
        await service.run(cross_project_spec)
    assert adapter.seen_specs == []
