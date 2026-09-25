from __future__ import annotations

from thoth.adapters.memory import (
    DeterministicRoleMemoryReviewer,
    KeywordMemoryReranker,
    LocalMemoryEmbedding,
    LocalRelationProjectionBuilder,
)
from thoth.application.services.full_project_memory import FullProjectMemoryService
from thoth.ports.ledger import LedgerPort
from thoth.ports.memory import FullMemoryStorePort, MemoryReviewerPort, MemoryStorePort
from thoth.ports.runtime import ClockPort, IdGeneratorPort


def create_full_project_memory_service(
    *,
    store: FullMemoryStorePort,
    candidates: MemoryStorePort,
    ledger: LedgerPort,
    clock: ClockPort,
    ids: IdGeneratorPort,
    reviewer: MemoryReviewerPort | None,
) -> FullProjectMemoryService:
    return FullProjectMemoryService(
        store=store,
        candidates=candidates,
        ledger=ledger,
        clock=clock,
        ids=ids,
        reviewer=reviewer or DeterministicRoleMemoryReviewer(),
        embedding=LocalMemoryEmbedding(),
        reranker=KeywordMemoryReranker(),
        projection_builder=LocalRelationProjectionBuilder(),
    )
