"""A slow model status read cannot block other LOCAL RPC work."""

from __future__ import annotations

import asyncio
import contextvars
import threading
from pathlib import Path

import pytest

from thoth.adapters.storage.workspace_setup import FilesystemWorkspaceSetup
from thoth.application.commands.workspace_setup import WorkspaceSetupHandlers
from thoth.domain.deployment_mode import DeploymentMode


@pytest.mark.asyncio
async def test_local_ready_offloads_account_check_and_preserves_context(tmp_path: Path) -> None:
    scope: contextvars.ContextVar[str] = contextvars.ContextVar("test_scope", default="none")
    scope.set("synthetic-scoped-read")
    started = threading.Event()
    release = threading.Event()
    observed: list[str] = []

    def slow_model_connected() -> bool:
        observed.append(scope.get())
        started.set()
        assert release.wait(2)
        return False

    handler = WorkspaceSetupHandlers(
        FilesystemWorkspaceSetup(tmp_path),
        deployment_mode=DeploymentMode.LOCAL,
        model_connected=slow_model_connected,
        workspace_id="workspace:" + "a" * 32,
    )
    pending = asyncio.create_task(handler.ready({}))
    try:
        assert await asyncio.wait_for(asyncio.to_thread(started.wait, 1), 1.5)
        assert not pending.done()
        heartbeat = asyncio.Event()
        asyncio.get_running_loop().call_soon(heartbeat.set)
        await asyncio.wait_for(heartbeat.wait(), 0.1)
    finally:
        release.set()
    ready = await asyncio.wait_for(pending, 2)
    assert observed == ["synthetic-scoped-read"]
    assert ready["workspace_readable"] is True
    assert ready["execution_ready"] is ready["ready"] is False
