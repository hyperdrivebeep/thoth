"""Preparation consumes real S2 storage/scoped sources, with a synthetic encoder."""

import asyncio
import threading
from pathlib import Path

import pytest
from tests.embedding_fixtures import PROJECT, fixture, sample_assets

from thoth.adapters.embeddings.download import FixedModelDownload
from thoth.adapters.runtime import SystemClock
from thoth.application.services.local_retrieval import LocalRetrievalService
from thoth.domain.deployment_mode import DeploymentMode
from thoth.domain.embedding_index import EmbeddingIndexError
from thoth.domain.model_asset import ModelAssetStatus


async def test_cancelled_read_does_not_multiply_pending_file_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    x, service = await setup(tmp_path)
    entered, release = threading.Event(), threading.Event()
    calls = 0
    original = service.assets.status

    def status(*, deadline: float | None = None) -> ModelAssetStatus:
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(3)
        return original(deadline=deadline)

    monkeypatch.setattr(service.assets, "status", status)
    try:
        first = asyncio.create_task(service.read_asset())
        assert await asyncio.to_thread(entered.wait, 3)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(service.read_asset())
        await asyncio.sleep(0)
        release.set()
        assert (await second)["asset_state"] == "READY"
        assert calls == 1
    finally:
        release.set()
        service.close()
        x.stores.close()


async def setup(tmp_path: Path):
    source, assets = sample_assets(tmp_path)
    await assets.import_directory(source)
    x = fixture(tmp_path / "workspace", enabled=False)
    service = LocalRetrievalService(
        mode=DeploymentMode.LOCAL,
        assets=assets,
        downloader=FixedModelDownload(assets),
        store=x.stores.embedding_index,
        projects=x.stores.projects,
        artifacts=x.service.artifacts,
        sessions=x.stores.auth,
        governance=x.stores.governance,
        clock=SystemClock(),
        dependency_available=lambda: True,
        factory=lambda _: x.service,
    )
    return x, service


async def test_explicit_prepare_publishes_current_source_only(tmp_path: Path):
    x, service = await setup(tmp_path)
    try:
        span = x.ingest()
        before = service.read_project(PROJECT)
        assert before["state"] == "OFF" and x.worker.calls == []
        enabled = service.configure(PROJECT, True, int(str(before["generation"])))
        result = await service.prepare(
            PROJECT, "operation:prepare", generation=int(str(enabled["generation"]))
        )
        assert result["state"] == "READY" and result["ready_sources"] == 1
        assert x.worker.calls == [span.exact_text]
        assert x.service.read_vectors(PROJECT, (span,))
        # Re-reading status does not trigger encoding or create a second index owner.
        assert service.read_project(PROJECT)["state"] == "READY"
        assert len(x.worker.calls) == 1 and service.index is x.service
    finally:
        service.close()
        x.stores.close()


async def test_disable_cancels_owned_work_and_cannot_publish_ready(tmp_path: Path):
    x, service = await setup(tmp_path)
    try:
        span = x.ingest()
        config = service.read_project(PROJECT)
        enabled = service.configure(PROJECT, True, int(str(config["generation"])))
        x.worker.release = asyncio.Event()
        task = asyncio.create_task(
            service.prepare(
                PROJECT, "operation:prepare", generation=int(str(enabled["generation"]))
            )
        )
        await asyncio.wait_for(x.worker.entered.wait(), 3)
        disabled = service.configure(PROJECT, False, int(str(enabled["generation"])))
        assert disabled["state"] == "CANCEL_REQUESTED"
        with pytest.raises(asyncio.CancelledError):
            await task
        assert service.read_project(PROJECT)["state"] == "OFF"
        assert (
            x.stores.embedding_index.ready(PROJECT, span.artifact_id, span.source_version_id)
            is None
        )
    finally:
        service.close()
        x.stores.close()


async def test_dependency_absent_or_stale_generation_does_not_construct_worker(tmp_path: Path):
    x, service = await setup(tmp_path)
    try:
        original = service.read_project(PROJECT)
        enabled = service.configure(PROJECT, True, int(str(original["generation"])))
        service.dependency_available = lambda: False
        with pytest.raises(EmbeddingIndexError, match="CONFIG_CONFLICT"):
            await service.prepare(PROJECT, "operation:stale", generation=0)
        with pytest.raises(EmbeddingIndexError, match="DEPENDENCY_UNAVAILABLE"):
            await service.prepare(
                PROJECT, "operation:missing", generation=int(str(enabled["generation"]))
            )
        assert service.index is None and x.worker.calls == []
    finally:
        service.close()
        x.stores.close()
