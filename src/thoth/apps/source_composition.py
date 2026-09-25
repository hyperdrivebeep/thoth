"""Composition of source mutation stores on one canonical SQLite owner."""

from __future__ import annotations

from pathlib import Path

from thoth.adapters.parsers.registry import default_parser_registry
from thoth.application.commands.sources import SourceCommandHandlers
from thoth.application.services.connector_service import ConnectorService
from thoth.application.services.evidence_graph_service import EvidenceGraphService
from thoth.application.services.ingestion_service import IngestionService
from thoth.application.services.scoped_artifacts import ScopedArtifactLedger
from thoth.application.services.source_time_service import SourceTimeService
from thoth.ports.ledger import ManagedLedgerPort
from thoth.ports.parser import ParserRegistryPort
from thoth.ports.runtime import ClockPort, IdGeneratorPort
from thoth.ports.store_bundle import StoreBundlePort


def create_source_time_service(
    stores: StoreBundlePort,
    artifacts: ScopedArtifactLedger,
    evidence_graph: EvidenceGraphService,
    clock: ClockPort,
    ids: IdGeneratorPort,
) -> SourceTimeService:
    return SourceTimeService(
        artifacts=artifacts,
        projects=stores.projects,
        evidence_graph=evidence_graph,
        ledger=stores.ledger,
        clock=clock,
        ids=ids,
    )


def create_ingestion_service(
    stores: StoreBundlePort,
    artifacts: ScopedArtifactLedger,
    clock: ClockPort,
    ids: IdGeneratorPort,
    parsers: ParserRegistryPort | None = None,
    source_time: SourceTimeService | None = None,
) -> IngestionService:
    return IngestionService(
        projects=stores.projects,
        objects=stores.objects,
        parsers=parsers or default_parser_registry(),
        artifacts=artifacts,
        clock=clock,
        ids=ids,
        scopes=artifacts.scopes,
        source_time_assessor=None if source_time is None else source_time.assess_ingestion,
    )


def create_source_handlers(
    *,
    workspace: Path,
    ledger: ManagedLedgerPort,
    stores: StoreBundlePort,
    ingestion: IngestionService,
    clock: ClockPort,
    evidence_graph: EvidenceGraphService,
    ids: IdGeneratorPort,
    connectors: ConnectorService,
    artifacts: ScopedArtifactLedger,
    source_time: SourceTimeService | None = None,
) -> SourceCommandHandlers:
    return SourceCommandHandlers(
        inbox_root=workspace / "inbox",
        ingestion=ingestion,
        artifacts=artifacts,
        governance=stores.governance,
        projects=stores.projects,
        ledger=ledger,
        clock=clock,
        evidence_graph=evidence_graph,
        ids=ids,
        connectors=connectors,
        resource_scopes=artifacts.scopes,
        source_time=source_time,
    )
