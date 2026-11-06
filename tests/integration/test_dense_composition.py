"""The composition that gives the research path its dense source, over real storage."""

from pathlib import Path
from typing import cast

import pytest
from tests.embedding_fixtures import PROJECT, fixture, sample_assets

from thoth.adapters.embeddings.download import FixedModelDownload
from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.application.services.local_retrieval import LocalRetrievalService
from thoth.application.services.research_analysis import ResearchAnalysis
from thoth.application.services.scoped_artifacts import ScopedArtifactLedger
from thoth.apps.local_retrieval_composition import create_dense_source, create_local_retrieval
from thoth.domain.deployment_mode import DeploymentMode
from thoth.protocol.registry import MethodRegistry


async def local(tmp_path: Path, mode: DeploymentMode = DeploymentMode.LOCAL):
    source, assets = sample_assets(tmp_path)
    await assets.import_directory(source)
    x = fixture(tmp_path / "workspace")
    service = LocalRetrievalService(
        mode=mode,
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


@pytest.mark.local_retrieval
async def test_after_a_restart_the_first_question_starts_the_encoder_and_the_next_uses_it(
    tmp_path: Path,
) -> None:
    pytest.importorskip("numpy", reason="numpy 없음(local-retrieval extra)")
    x, service = await local(tmp_path)
    try:
        spans = tuple(x.ingest(name) for name in ("a", "b"))
        x.service.reconcile(PROJECT)
        await x.service.drain(PROJECT)
        # The index is READY, but this process has not built an index service (a restart).
        assert service.index is None
        provider = create_dense_source(service, x.stores.embedding_index)
        assert provider is not None
        first = await provider.material(PROJECT, "which lantern", spans)
        assert first is not None and first.unavailable_reason == "EMBEDDING_NOT_PREPARED"
        assert provider.warming is not None
        await provider.warming
        assert service.index is x.service
        second = await provider.material(PROJECT, "which lantern", spans)
        assert second is not None and second.unavailable_reason is None and len(second.scores) == 2
    finally:
        service.close()
        x.stores.close()


async def test_only_a_local_workspace_gets_a_dense_source(tmp_path: Path) -> None:
    x, hosted = await local(tmp_path, DeploymentMode.HOSTED_REVIEW)
    try:
        assert create_dense_source(hosted, x.stores.embedding_index) is None
    finally:
        x.stores.close()


async def test_the_runtime_composition_hands_the_provider_to_the_research_analysis(
    tmp_path: Path,
) -> None:
    x = fixture(tmp_path / "workspace")
    analysis = ResearchAnalysis.__new__(ResearchAnalysis)
    analysis.dense_source = None
    try:
        service = create_local_retrieval(
            MethodRegistry(),
            x.stores,
            cast(ScopedArtifactLedger, x.service.artifacts),
            SystemClock(),
            UuidIdGenerator(),
            tmp_path / "workspace",
            DeploymentMode.LOCAL,
            analysis,
        )
        assert service is not None and analysis.dense_source is not None
    finally:
        x.stores.close()
