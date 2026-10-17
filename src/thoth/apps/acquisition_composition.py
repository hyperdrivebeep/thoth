"""Compose acquisition services without owning their execution or storage lifetime."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from thoth.adapters.connectors import ConnectorRegistry, GitReadConnector, LocalFileConnector
from thoth.adapters.connectors.project_public_web import ProjectPublicWebConnector
from thoth.adapters.storage.workspace_setup import read_setup
from thoth.application.services.acquisition_coordinator import AcquisitionCoordinator
from thoth.application.services.connector_service import ConnectorService
from thoth.application.services.control_record_service import ControlRecordService
from thoth.application.services.critical_counter_search_coordinator import (
    CriticalCounterSearchCoordinator,
)
from thoth.application.services.evidence_graph_service import EvidenceGraphService
from thoth.application.services.ingestion_service import IngestionService
from thoth.application.services.investigation_service import InvestigationService
from thoth.application.services.policy_gate import PolicyGate
from thoth.application.services.research_identity_service import ResearchIdentityService
from thoth.application.services.scoped_artifacts import ScopedArtifactLedger
from thoth.domain.policy import preferred_hosts_from_payload
from thoth.domain.workspace_setup import WorkspaceSetupState
from thoth.ports.acquisition import AcquisitionTraceStorePort, EvidenceUnitOfWorkPort
from thoth.ports.evidence_graph import EvidenceGraphStorePort
from thoth.ports.governance import GovernanceStorePort
from thoth.ports.ledger import ManagedLedgerPort
from thoth.ports.project import ProjectStorePort
from thoth.ports.runtime import ClockPort, IdGeneratorPort
from thoth.ports.store_bundle import StoreBundlePort


@dataclass(frozen=True)
class AcquisitionComponents:
    effective_connectors: ConnectorRegistry
    workspace_setup: Callable[[], WorkspaceSetupState]
    connector_service: ConnectorService
    acquisition_coordinator: AcquisitionCoordinator
    critical_counter_search: CriticalCounterSearchCoordinator


def create_acquisition_components(
    *,
    workspace: Path,
    connector_registry: ConnectorRegistry | None,
    stores: StoreBundlePort,
    governance: GovernanceStorePort,
    projects: ProjectStorePort,
    ingestion_service: IngestionService,
    evidence_graph_service: EvidenceGraphService,
    acquisition_fault_injector: Callable[[str], None] | None,
    artifact_ledger: ScopedArtifactLedger,
    control_service: ControlRecordService,
    clock: ClockPort,
    ids: IdGeneratorPort,
    evidence_unit_of_work: EvidenceUnitOfWorkPort,
    investigation_service: InvestigationService,
    acquisition_trace_store: AcquisitionTraceStorePort,
    research_identity: ResearchIdentityService,
    evidence_graph_store: EvidenceGraphStorePort,
    ledger: ManagedLedgerPort,
) -> AcquisitionComponents:
    def hosts_for(project_id: str) -> tuple[str, ...]:
        stored = governance.read_policy(project_id)
        if stored is None:
            return ()
        return preferred_hosts_from_payload(stored.payload)

    managed_web = ProjectPublicWebConnector(hosts_for)
    if connector_registry is None:
        effective_connectors = ConnectorRegistry(
            (
                LocalFileConnector(workspace / "inbox"),
                GitReadConnector(workspace),
                managed_web,
            )
        )
    else:
        effective_connectors = connector_registry

    def workspace_setup() -> WorkspaceSetupState:
        return read_setup(workspace)

    connector_service = ConnectorService(
        registry=effective_connectors,
        ingestion=ingestion_service,
        evidence_graph=evidence_graph_service,
        projects=projects,
        policies=PolicyGate(policies=governance, clock=clock),
        unit_of_work=stores.acquisition_uow(
            fault_injector=acquisition_fault_injector,
            resource_scopes=artifact_ledger.scopes,
        ),
        controls=control_service,
        clock=clock,
        ids=ids,
        workspace_setup=workspace_setup,
    )
    acquisition_coordinator = AcquisitionCoordinator(
        connectors=connector_service,
        evidence_graph=evidence_graph_service,
        evidence_unit_of_work=evidence_unit_of_work,
        investigations=investigation_service,
        traces=acquisition_trace_store,
        policies=governance,
        clock=clock,
        ids=ids,
    )
    critical_counter_search = CriticalCounterSearchCoordinator(
        research=research_identity,
        connectors=connector_service,
        evidence_graph=evidence_graph_service,
        evidence_unit_of_work=evidence_unit_of_work,
        evidence_store=evidence_graph_store,
        artifacts=artifact_ledger,
        investigations=investigation_service,
        traces=acquisition_trace_store,
        governance=governance,
        policies=governance,
        ledger=ledger,
        clock=clock,
        ids=ids,
    )
    return AcquisitionComponents(
        effective_connectors=effective_connectors,
        workspace_setup=workspace_setup,
        connector_service=connector_service,
        acquisition_coordinator=acquisition_coordinator,
        critical_counter_search=critical_counter_search,
    )
