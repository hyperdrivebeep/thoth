"""A disconnected LOCAL model does not erase or lock existing scoped research."""

from pathlib import Path

import pytest
from tests.integration.storage_coverage_helpers import request, value

from thoth.adapters.models.catalog import StaticModelCatalog
from thoth.adapters.models.codex_broker import broker_for_workspace, close_workspace_broker
from thoth.adapters.models.local_credentials import LocalModelCredentials
from thoth.adapters.models.registry import RegisteredModelResolver
from thoth.apps.runtime import create_runtime


@pytest.mark.asyncio
async def test_local_ready_and_project_read_survive_reopen_without_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    synthetic_home = tmp_path / "synthetic-home"
    synthetic_home.mkdir()
    for name in ("HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "XDG_DATA_HOME", "CODEX_HOME"):
        monkeypatch.setenv(name, str(synthetic_home / name.lower()))

    def disconnected(_self: LocalModelCredentials) -> list[dict[str, object]]:
        return []

    monkeypatch.setattr(LocalModelCredentials, "account_connections", disconnected)
    workspace = tmp_path / "persistent"
    model_resolver = RegisteredModelResolver()
    model_catalog = StaticModelCatalog()
    first = create_runtime(workspace, model_resolver=model_resolver, model_catalog=model_catalog)
    try:
        initial = value(await first.bus.query(request("workspace/ready", "first-ready", {})))
        assert initial["ready"] is False
        assert initial["setup_complete"] is False
        assert initial["workspace_readable"] is True
        assert initial["execution_ready"] is False
        assert initial["model_connected"] is False
        assert isinstance(initial["workspace_id"], str)
        value(
            await first.bus.dispatch(
                request(
                    "project/create",
                    "persistent-project",
                    {
                        "project_id": "project:existing",
                        "name": "Existing",
                        "cutoff_at": "2026-09-01T00:00:00Z",
                    },
                )
            )
        )
        value(
            await first.bus.dispatch(
                request("workspace/setup/update", "consent", {"internet_consent": "DENIED"})
            )
        )
    finally:
        first.close()

    reopened = create_runtime(workspace, model_resolver=model_resolver, model_catalog=model_catalog)
    try:
        after = value(await reopened.bus.query(request("workspace/ready", "after-ready", {})))
        assert after["workspace_id"] == initial["workspace_id"]
        assert after["setup_complete"] is True
        assert after["workspace_readable"] is True
        assert after["execution_ready"] is after["ready"] is False
        projects = value(await reopened.bus.query(request("project/list", "saved-projects", {})))
        assert any(item["project_id"] == "project:existing" for item in projects["projects"])
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_corrupt_setup_reports_hold_but_keeps_saved_project_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    synthetic_home = tmp_path / "synthetic-home"
    synthetic_home.mkdir()
    for name in ("HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "XDG_DATA_HOME", "CODEX_HOME"):
        monkeypatch.setenv(name, str(synthetic_home / name.lower()))
    workspace = tmp_path / "damaged"
    resolver = RegisteredModelResolver()
    catalog = StaticModelCatalog()
    first = create_runtime(workspace, model_resolver=resolver, model_catalog=catalog)
    try:
        value(
            await first.bus.dispatch(
                request(
                    "project/create",
                    "damaged-project",
                    {
                        "project_id": "project:saved",
                        "name": "Saved",
                        "cutoff_at": "2026-09-01T00:00:00Z",
                    },
                )
            )
        )
        value(
            await first.bus.dispatch(
                request(
                    "workspace/setup/update",
                    "saved-consent",
                    {"internet_consent": "DENIED"},
                )
            )
        )
    finally:
        first.close()
    source = workspace / "workspace-setup.json"
    source.write_bytes(b"{broken-but-preserved")
    reopened = create_runtime(workspace, model_resolver=resolver, model_catalog=catalog)
    try:
        ready = value(await reopened.bus.query(request("workspace/ready", "damaged-ready", {})))
        assert ready["setup_status"] == "CORRUPT"
        assert ready["reason_code"] == "WORKSPACE_SETUP_CORRUPT"
        assert ready["setup"] is None
        assert ready["workspace_readable"] is True
        assert ready["execution_ready"] is ready["ready"] is False
        setup = value(await reopened.bus.query(request("workspace/setup/read", "damaged-read", {})))
        assert setup["setup_status"] == "CORRUPT"
        assert "internet_consent" not in setup
        failed = await reopened.bus.dispatch(
            request(
                "workspace/setup/update",
                "unsafe-overwrite",
                {"internet_consent": "ALLOWED"},
            )
        )
        assert failed.error is not None
        assert failed.error.message == "WORKSPACE_SETUP_CORRUPT"
        assert source.read_bytes() == b"{broken-but-preserved"
        projects = value(await reopened.bus.query(request("project/list", "damaged-projects", {})))
        assert any(item["project_id"] == "project:saved" for item in projects["projects"])
    finally:
        reopened.close()


def test_two_local_runtimes_share_broker_until_last_owner_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    synthetic_home = tmp_path / "synthetic-home"
    synthetic_home.mkdir()
    for name in ("HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "XDG_DATA_HOME", "CODEX_HOME"):
        monkeypatch.setenv(name, str(synthetic_home / name.lower()))
    workspace = tmp_path / "shared"
    model_resolver = RegisteredModelResolver()
    model_catalog = StaticModelCatalog()
    first = create_runtime(workspace, model_resolver=model_resolver, model_catalog=model_catalog)
    second = create_runtime(workspace, model_resolver=model_resolver, model_catalog=model_catalog)
    shared = broker_for_workspace(workspace)
    first_closed = second_closed = False
    try:
        first.close()
        first_closed = True
        assert broker_for_workspace(workspace) is shared
        second.close()
        second_closed = True
        fresh = broker_for_workspace(workspace)
        assert fresh is not shared
    finally:
        if not first_closed:
            first.close()
        if not second_closed:
            second.close()
        close_workspace_broker(workspace)
