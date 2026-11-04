"""Wire the optional local preparation capability without loading native dependencies."""

from importlib.util import find_spec
from pathlib import Path

from thoth.adapters.embeddings.assets import LocalModelAssets
from thoth.adapters.embeddings.download import FixedModelDownload
from thoth.adapters.embeddings.process_worker import ProcessEmbeddingWorker
from thoth.application.commands.local_retrieval import LocalRetrievalHandlers
from thoth.application.services.embedding_index_service import EmbeddingIndexService
from thoth.application.services.local_retrieval import LocalRetrievalService
from thoth.application.services.scoped_artifacts import ScopedArtifactLedger
from thoth.apps.embedding_composition import create_embedding_index
from thoth.domain.deployment_mode import DeploymentMode
from thoth.domain.model_asset import EmbeddingAssetDescriptor
from thoth.ports.embedding_index import EmbeddingIndexProviderPort
from thoth.ports.runtime import ClockPort, IdGeneratorPort
from thoth.ports.store_bundle import StoreBundlePort
from thoth.protocol.registry import MethodRegistry


def create_local_retrieval(
    registry: MethodRegistry,
    stores: StoreBundlePort,
    artifacts: ScopedArtifactLedger,
    clock: ClockPort,
    ids: IdGeneratorPort,
    workspace: Path,
    mode: DeploymentMode,
) -> LocalRetrievalService | None:
    if not isinstance(stores, EmbeddingIndexProviderPort):
        return None
    index_store = stores.embedding_index
    assets = LocalModelAssets(workspace / "models")

    def factory(descriptor: EmbeddingAssetDescriptor) -> EmbeddingIndexService:
        worker = ProcessEmbeddingWorker(
            "thoth.adapters.embeddings.onnx_encoder:create_encoder", descriptor=descriptor
        )
        return create_embedding_index(stores, index_store, artifacts, clock, ids, worker)

    service = LocalRetrievalService(
        mode=mode,
        assets=assets,
        downloader=FixedModelDownload(assets),
        store=index_store,
        projects=stores.projects,
        artifacts=artifacts,
        sessions=stores.auth,
        governance=stores.governance,
        clock=clock,
        dependency_available=lambda: all(
            find_spec(name) is not None for name in ("numpy", "onnxruntime", "tokenizers")
        ),
        factory=factory,
    )
    handlers = LocalRetrievalHandlers(service)
    for name, handler in (
        ("model/localAsset/read", handlers.read_asset),
        ("model/localAsset/import", handlers.import_asset),
        ("model/localAsset/install", handlers.install_asset),
        ("project/retrieval/experiment/read", handlers.read_project),
        ("project/retrieval/experiment/configure", handlers.configure),
        ("project/retrieval/experiment/prepare", handlers.prepare),
        ("project/embeddingIndex/retry", handlers.retry),
    ):
        registry.register(name, handler)
    return service
