"""Login completion refreshes the model list once, through an explicit workspace command."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.integration.storage_coverage_helpers import request, value

from thoth.adapters.models import codex_broker
from thoth.adapters.models.catalog import StaticModelCatalog
from thoth.apps.runtime import create_runtime
from thoth.domain.model_settings import ModelOption

pytestmark = pytest.mark.usefixtures("xai_http_guard")


class RemoteListCatalog(StaticModelCatalog):
    """Advertises its options only after a remote refresh, like the Codex catalog."""

    def __init__(self) -> None:
        super().__init__()
        self.refreshes = 0

    def refresh(self) -> None:
        self.refreshes += 1
        self._options = (
            ModelOption(
                provider="codex-oauth",
                model="gpt-test",
                reasoning_efforts=("high",),
                default_effort="high",
                capability_source="controlled-refresh-catalog",
            ),
        )


class _ConnectedBroker:
    def local_status(self) -> codex_broker.CodexBrokerState:
        return codex_broker.CodexBrokerState(True, True, "EXECUTION_UNVERIFIED")


def _connected_codex(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "synthetic-home"
    (home / ".codex").mkdir(parents=True)
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))

    def connected_broker(_workspace: Path) -> _ConnectedBroker:
        return _ConnectedBroker()

    monkeypatch.setattr(codex_broker, "broker_for_workspace", connected_broker)


@pytest.mark.asyncio
async def test_connected_login_needs_one_explicit_refresh_before_workspace_is_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _connected_codex(monkeypatch, tmp_path)
    catalog = RemoteListCatalog()
    runtime = create_runtime(tmp_path / "a", model_catalog=catalog)
    try:
        before = value(await runtime.bus.query(request("workspace/ready", "ready-before", {})))
        assert before["model_connected"] is False
        for index in range(3):
            value(
                await runtime.bus.query(
                    request(
                        "model/credential/list", f"list-{index}", {"project_id": "system:workspace"}
                    )
                )
            )
        assert catalog.refreshes == 0

        refreshed = value(
            await runtime.bus.dispatch(
                request("model/catalog/refresh", "refresh-1", {"project_id": "system:workspace"})
            )
        )
        assert catalog.refreshes == 1
        assert refreshed["model_option_count"] == 1
        openai = next(row for row in refreshed["accounts"] if row["provider"] == "openai")
        assert openai["available_model_providers"] == ["codex-oauth"]

        after = value(await runtime.bus.query(request("workspace/ready", "ready-after", {})))
        assert after["model_connected"] is True
        value(
            await runtime.bus.query(
                request("model/credential/list", "list-after", {"project_id": "system:workspace"})
            )
        )
        assert catalog.refreshes == 1
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_refresh_with_no_remote_catalog_is_a_valid_empty_result(tmp_path: Path) -> None:
    runtime = create_runtime(tmp_path / "a", model_catalog=StaticModelCatalog())
    try:
        refreshed = value(
            await runtime.bus.dispatch(
                request(
                    "model/catalog/refresh", "refresh-empty", {"project_id": "system:workspace"}
                )
            )
        )
        assert refreshed["model_option_count"] == 0
        ready = value(await runtime.bus.query(request("workspace/ready", "ready", {})))
        assert ready["model_connected"] is False
    finally:
        runtime.close()
