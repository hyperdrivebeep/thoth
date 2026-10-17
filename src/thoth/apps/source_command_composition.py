"""Compose the ProjectPack and normal source intake command entry points."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from thoth.adapters.projectpacks import FilesystemProjectPackLoader
from thoth.application.commands.projectpacks import ProjectPackCommandHandlers
from thoth.application.commands.sources import SourceCommandHandlers
from thoth.application.services.connector_service import ConnectorService
from thoth.application.services.evidence_graph_service import EvidenceGraphService
from thoth.application.services.ingestion_service import IngestionService
from thoth.application.services.scoped_artifacts import ScopedArtifactLedger
from thoth.application.services.source_time_service import SourceTimeService
from thoth.apps.projectpack_execution import ProjectPackExecutionFactory
from thoth.apps.source_composition import create_source_handlers
from thoth.ports.ledger import ManagedLedgerPort
from thoth.ports.model import ModelResolverPort
from thoth.ports.runtime import ClockPort, IdGeneratorPort
from thoth.ports.store_bundle import StoreBundlePort, StoreFactoryPort


@dataclass(frozen=True)
class SourceCommands:
    projectpack_handlers: ProjectPackCommandHandlers
    source_handlers: SourceCommandHandlers


def create_source_commands(
    *,
    artifact_ledger: ScopedArtifactLedger,
    clock: ClockPort,
    connector_service: ConnectorService,
    effective_models: ModelResolverPort,
    evidence_graph_service: EvidenceGraphService,
    ids: IdGeneratorPort,
    ingestion_service: IngestionService,
    ledger: ManagedLedgerPort,
    projectpack_root: Path | None,
    source_time_service: SourceTimeService,
    storage_factory: StoreFactoryPort | None,
    stores: StoreBundlePort,
    workspace: Path,
) -> SourceCommands:
    projectpack_handlers = ProjectPackCommandHandlers(
        loader=FilesystemProjectPackLoader(projectpack_root or Path("examples/projectpacks")),
        executions=ProjectPackExecutionFactory(
            storage_factory=storage_factory,
            workspace=workspace,
            models=effective_models,
        ),
    )
    source_handlers = create_source_handlers(
        stores=stores,
        workspace=workspace,
        ledger=ledger,
        ingestion=ingestion_service,
        clock=clock,
        evidence_graph=evidence_graph_service,
        ids=ids,
        connectors=connector_service,
        artifacts=artifact_ledger,
        source_time=source_time_service,
    )
    return SourceCommands(
        projectpack_handlers=projectpack_handlers,
        source_handlers=source_handlers,
    )
