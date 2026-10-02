"""Starting the app fetches a missing model list in the background and never waits for it."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from tests.integration.storage_coverage_helpers import request, value
from tests.unit.models.test_codex_isolated_broker import make_synthetic_broker

from thoth.adapters.models import codex_broker
from thoth.adapters.models.catalog_store import CatalogSnapshotStore
from thoth.apps.runtime import create_runtime

pytestmark = pytest.mark.usefixtures("xai_http_guard")


def wait_for(condition: object, seconds: float = 5.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if callable(condition) and condition():
            return True
        time.sleep(0.05)
    return False


@pytest.mark.asyncio
async def test_a_missing_list_is_fetched_once_at_start_in_the_background(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, server = make_synthetic_broker(tmp_path, monkeypatch)
    workspace = broker.profile.workspace
    store = CatalogSnapshotStore(workspace)
    for saved in store.root.glob("*.json"):
        saved.unlink()
    broker._catalog = None  # pyright: ignore[reportPrivateUsage]
    broker._cached = None  # pyright: ignore[reportPrivateUsage]
    server.methods.clear()
    monkeypatch.setitem(codex_broker._BROKERS, workspace.resolve(), broker)  # pyright: ignore[reportPrivateUsage]
    entered = threading.Event()
    release = threading.Event()
    real_call = server.call

    def held_call(method: str, params: dict[str, object]) -> dict[str, object]:
        if method == "model/list":
            entered.set()
            # Safety bound only, so a regression cannot hang the suite; not a timing assertion.
            assert release.wait(30)
        return real_call(method, params)

    monkeypatch.setattr(server, "call", held_call)
    runtime = create_runtime(workspace)
    try:
        # create_runtime returned while the list request is still held by the server.
        assert not release.is_set()
        assert wait_for(entered.is_set)
        assert not list(store.root.glob("*.json"))
        release.set()
        assert wait_for(lambda: list(store.root.glob("*.json")))
        assert server.methods.count("model/list") == 1
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "p",
                    {"project_id": "p", "name": "P", "cutoff_at": "2026-09-24T00:00:00Z"},
                )
            )
        )
        read = value(
            await runtime.bus.query(request("model/settings/read", "r", {"project_id": "p"}))
        )
        assert [o["model"] for o in read["model_options"]] == ["synthetic-codex"]
        (row,) = [r for r in read["catalog_status"] if r["provider"] == "codex-oauth"]
        assert row["status"] == "ACTIVE" and row["source"] == "PROVIDER_LIST"
        assert server.methods.count("model/list") == 1
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_list_fetched_today_is_not_fetched_again_at_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, server = make_synthetic_broker(tmp_path, monkeypatch)
    monkeypatch.setitem(codex_broker._BROKERS, broker.profile.workspace.resolve(), broker)  # pyright: ignore[reportPrivateUsage]
    server.methods.clear()
    runtime = create_runtime(broker.profile.workspace)
    try:
        time.sleep(0.5)
        assert server.methods.count("model/list") == 0
    finally:
        runtime.close()
