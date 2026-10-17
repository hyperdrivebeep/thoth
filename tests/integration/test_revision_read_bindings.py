"""Observe real RPC decoration: legacy diff delegates, preview uses the replacement handler."""

from pathlib import Path

import pytest
from pydantic import JsonValue
from tests.integration.test_revision_full_rpc import (
    test_revision_atomic_changeset_merge_restore_and_baseline as run_revision_rpc_scenario,
)

from thoth.application.commands.restore import RestoreHandlers
from thoth.application.commands.revisions_full import RevisionFullHandlers


@pytest.mark.asyncio
async def test_real_revision_rpc_reaches_legacy_diff_but_not_legacy_preview(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, int] = {
        "legacy_diff": 0,
        "legacy_preview": 0,
        "decorated_diff": 0,
        "decorated_preview": 0,
    }
    original_diff = RevisionFullHandlers.diff_read
    original_preview = RevisionFullHandlers.restore_preview
    decorated_diff = RestoreHandlers.diff
    decorated_preview = RestoreHandlers.preview

    async def old_diff(
        self: RevisionFullHandlers, value: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        calls["legacy_diff"] += 1
        assert value.get("contract_version") != 2
        return await original_diff(self, value)

    async def old_preview(
        self: RevisionFullHandlers, value: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        calls["legacy_preview"] += 1
        return await original_preview(self, value)

    async def diff(self: RestoreHandlers, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        calls["decorated_diff"] += 1
        return await decorated_diff(self, value)

    async def preview(self: RestoreHandlers, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        calls["decorated_preview"] += 1
        return await decorated_preview(self, value)

    monkeypatch.setattr(RevisionFullHandlers, "diff_read", old_diff)
    monkeypatch.setattr(RevisionFullHandlers, "restore_preview", old_preview)
    monkeypatch.setattr(RestoreHandlers, "diff", diff)
    monkeypatch.setattr(RestoreHandlers, "preview", preview)
    await run_revision_rpc_scenario(tmp_path)
    assert calls["legacy_diff"] > 0 and calls["decorated_diff"] == calls["legacy_diff"]
    assert calls["decorated_preview"] > 0 and calls["legacy_preview"] == 0
