"""Optional explicit embedding capability; normal runtime construction stays off."""

from dataclasses import replace

from thoth.application.services.embedding_index_service import EmbeddingIndexService
from thoth.application.services.scoped_artifacts import ScopedArtifactLedger
from thoth.apps.runtime_types import RuntimeWithLedger
from thoth.ports.embedding_index import (
    BoundedEmbeddingWorkerPort,
    EmbeddingIndexProviderPort,
    EmbeddingIndexStorePort,
)
from thoth.ports.ledger import ManagedLedgerPort
from thoth.ports.runtime import ClockPort, IdGeneratorPort
from thoth.ports.store_bundle import StoreBundlePort


def create_embedding_index(
    stores: StoreBundlePort,
    index_store: EmbeddingIndexStorePort,
    artifacts: ScopedArtifactLedger,
    clock: ClockPort,
    ids: IdGeneratorPort,
    worker: BoundedEmbeddingWorkerPort,
) -> EmbeddingIndexService:
    return EmbeddingIndexService(
        store=index_store,
        artifacts=artifacts,
        governance=stores.governance,
        projects=stores.projects,
        worker=worker,
        uow=stores.immediate_uow,
        clock=clock,
        ids=ids,
    )


def attach_embedding[LedgerT: ManagedLedgerPort](
    runtime: RuntimeWithLedger[LedgerT],
    stores: StoreBundlePort,
    artifacts: ScopedArtifactLedger,
    clock: ClockPort,
    ids: IdGeneratorPort,
    worker: BoundedEmbeddingWorkerPort | None,
) -> RuntimeWithLedger[LedgerT]:
    if worker is None:
        return runtime
    try:
        if not isinstance(stores, EmbeddingIndexProviderPort):
            raise ValueError("EMBEDDING_STORE_UNSUPPORTED")
        service = create_embedding_index(
            stores, stores.embedding_index, artifacts, clock, ids, worker
        )
        if runtime.local_retrieval is not None:
            runtime.local_retrieval.bind_index(service)
        return replace(runtime, embedding_index=service)
    except BaseException:
        runtime.close()
        raise
