"""Optional explicit embedding capability; normal runtime construction stays off."""

from dataclasses import replace

from thoth.application.services.embedding_index_service import EmbeddingIndexService
from thoth.application.services.scoped_artifacts import ScopedArtifactLedger
from thoth.apps.runtime_types import RuntimeWithLedger
from thoth.ports.embedding_index import BoundedEmbeddingWorkerPort, EmbeddingIndexProviderPort
from thoth.ports.ledger import ManagedLedgerPort
from thoth.ports.runtime import ClockPort, IdGeneratorPort
from thoth.ports.store_bundle import StoreBundlePort


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
        service = EmbeddingIndexService(
            store=stores.embedding_index,
            artifacts=artifacts,
            governance=stores.governance,
            projects=stores.projects,
            worker=worker,
            uow=stores.immediate_uow,
            clock=clock,
            ids=ids,
        )
        return replace(runtime, embedding_index=service)
    except BaseException:
        runtime.close()
        raise
