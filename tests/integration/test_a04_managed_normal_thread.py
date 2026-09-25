from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from qa.scenarios.n02_managed_normal_thread import run_managed_normal_thread
from tests.integration.test_four_projectpack_portability import GenericProjectPackModel

from thoth.adapters.sandbox import E2BManagedSandboxAdapter, default_sandbox_factory_registry
from thoth.adapters.sandbox.e2b import ManagedSandboxFactory


class FakeCommandResult:
    stdout = "THOTH_MANAGED_NORMAL_THREAD_PASS\n"
    stderr = ""
    exit_code = 0
    error = None


class FakeCommands:
    def run(self, command: str, *, timeout: float | None = 60) -> FakeCommandResult:
        assert command == "printf THOTH_MANAGED_NORMAL_THREAD_PASS"
        assert timeout == 30
        return FakeCommandResult()


class FakeFiles:
    def __init__(self) -> None:
        self.writes: list[tuple[str, bytes]] = []

    def write(self, path: str, data: bytes) -> object:
        assert path.startswith("/inputs/")
        assert data
        self.writes.append((path, data))
        return object()


class FakeManagedSandbox:
    def __init__(self) -> None:
        self._commands = FakeCommands()
        self._files = FakeFiles()
        self.killed = False

    @property
    def commands(self) -> FakeCommands:
        return self._commands

    @property
    def files(self) -> FakeFiles:
        return self._files

    def kill(self) -> bool:
        self.killed = True
        return True


def fake_factory(
    *,
    template: str,
    timeout: int,
    metadata: dict[str, str],
    secure: bool,
    allow_internet_access: bool,
) -> FakeManagedSandbox:
    del template, timeout, metadata, secure, allow_internet_access
    return REMOTE


REMOTE = FakeManagedSandbox()


@pytest.mark.asyncio
async def test_managed_registry_enters_normal_thread_and_cleans_up(tmp_path: Path) -> None:
    registered = default_sandbox_factory_registry().create("managed-e2b", tmp_path)
    assert isinstance(registered, E2BManagedSandboxAdapter)

    adapter = E2BManagedSandboxAdapter(factory=cast(ManagedSandboxFactory, fake_factory))
    result = await run_managed_normal_thread(
        workspace=tmp_path / "managed",
        adapter=adapter,
        model=GenericProjectPackModel(),
    )

    stages = cast(dict[str, object], result.manifest["stages"])
    sandbox = cast(dict[str, object], stages["sandbox_outcome"])
    assert sandbox["terminal_state"] == "COMPLETED"
    assert sandbox["runtime_profile"] == "MANAGED"
    assert sandbox["network_policy"] == "DENY_ALL"
    assert sandbox["exit_code"] == 0
    assert sandbox["cleanup_state"] == "DESTROYED"
    assert len(cast(str, sandbox["policy_digest"])) == 64
    assert len(cast(str, sandbox["current_head_set_digest"])) == 64
    assert len(cast(str, sandbox["input_context_digest"])) == 64
    assert cast(int, sandbox["observation_count"]) >= 1
    assert sandbox["outcome_present"] is True
    assert sandbox["hypothesis_action_revision_present"] is True
    assert result.manifest["active_sandbox_count"] == 0
    assert REMOTE.killed is True
    assert REMOTE.files.writes
    assert result.manifest["r3_or_r4_executed"] is False
