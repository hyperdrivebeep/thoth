"""The tool install command runs through the normal RPC path with an injected installer."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.integration.storage_coverage_helpers import request, value

from thoth.adapters.models.tool_installers import ToolInstallerRegistry
from thoth.apps.runtime import create_runtime

pytestmark = pytest.mark.usefixtures("xai_http_guard")


class FakeInstaller:
    def __init__(self) -> None:
        self.runs = 0

    def install(self) -> dict[str, object]:
        self.runs += 1
        return {"installed": True, "version": "9.9.9"}


def _runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, installer: FakeInstaller):
    registry = ToolInstallerRegistry()
    registry.register("fake-tool", installer)

    def registry_for(_workspace: Path | None) -> ToolInstallerRegistry:
        return registry

    monkeypatch.setattr(
        "thoth.apps.research_entry_composition.default_tool_installer_registry", registry_for
    )
    return create_runtime(tmp_path / "a")


@pytest.mark.asyncio
async def test_install_runs_the_named_tool_once_and_returns_refreshed_accounts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer = FakeInstaller()
    runtime = _runtime(tmp_path, monkeypatch, installer)
    try:
        result = value(
            await runtime.bus.dispatch(
                request(
                    "model/tooling/install",
                    "install-1",
                    {"project_id": "system:workspace", "tool_id": "fake-tool"},
                )
            )
        )
        assert installer.runs == 1
        assert result["tool_id"] == "fake-tool" and result["installed"] is True
        assert result["version"] == "9.9.9"
        assert {row["provider"] for row in result["accounts"]} == {"openai", "anthropic", "xai"}
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_unknown_or_malformed_tool_ids_are_rejected_before_any_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer = FakeInstaller()
    runtime = _runtime(tmp_path, monkeypatch, installer)
    try:
        unknown = await runtime.bus.dispatch(
            request(
                "model/tooling/install",
                "install-unknown",
                {"project_id": "system:workspace", "tool_id": "other"},
            )
        )
        assert unknown.error is not None and unknown.error.message == "MODEL_TOOL_UNKNOWN"
        for index, bad in enumerate(("", "..\\evil", "A B", None, 7)):
            rejected = await runtime.bus.dispatch(
                request(
                    "model/tooling/install",
                    f"install-bad-{index}",
                    {"project_id": "system:workspace", "tool_id": bad},
                )
            )
            assert rejected.error is not None
            assert rejected.error.message == "MODEL_TOOL_ID_INVALID"
        assert installer.runs == 0
    finally:
        runtime.close()
