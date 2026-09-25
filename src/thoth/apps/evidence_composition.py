"""Wire Evidence writes to the same ledger-owned transaction."""

from __future__ import annotations

from collections.abc import Callable

from thoth.application.services.evidence_graph_service import EvidenceGraphService
from thoth.application.services.scoped_artifacts import ScopedArtifactLedger
from thoth.ports.acquisition import EvidenceUnitOfWorkPort
from thoth.ports.evidence_graph import EvidenceGraphStorePort
from thoth.ports.ledger import ManagedLedgerPort
from thoth.ports.runtime import ClockPort, IdGeneratorPort
from thoth.ports.store_bundle import StoreBundlePort


def create_evidence_components(
    *,
    ledger: ManagedLedgerPort,
    stores: StoreBundlePort,
    store: EvidenceGraphStorePort,
    artifacts: ScopedArtifactLedger,
    clock: ClockPort,
    ids: IdGeneratorPort,
    fault_injector: Callable[[str], None] | None,
) -> tuple[EvidenceUnitOfWorkPort, EvidenceGraphService]:
    unit_of_work = stores.evidence_uow(
        resource_scopes=artifacts.scopes, fault_injector=fault_injector
    )
    return unit_of_work, EvidenceGraphService(
        store=store,
        artifacts=artifacts,
        unit_of_work=unit_of_work,
        ledger=ledger,
        clock=clock,
        ids=ids,
    )
