"""Assemble the research entry and bus, returning their resources on startup failure."""

from __future__ import annotations

from pathlib import Path

from thoth.application.commands import ThreadCommandHandlers
from thoth.application.commands.research_operations import register_research_operations
from thoth.application.services import FieldMeasurementService
from thoth.application.services.connector_service import ConnectorService
from thoth.application.services.full_project_memory import FullProjectMemoryService
from thoth.application.services.scoped_artifacts import ScopedArtifactLedger
from thoth.apps.research_entry_composition import ResearchEntryComposition, create_research_entry
from thoth.apps.research_runtime import ResearchComponents
from thoth.apps.resource_scope_composition import scope_bus
from thoth.apps.runtime_registration import RuntimeRegistration
from thoth.apps.runtime_types import RuntimeWithLedger
from thoth.domain.deployment_mode import DeploymentMode
from thoth.ports.criterion_contract import CriterionContractStorePort
from thoth.ports.evidence_graph import EvidenceGraphStorePort
from thoth.ports.ledger import ManagedLedgerPort
from thoth.ports.model import ModelResolverPort
from thoth.ports.model_catalog import ModelCatalogPort
from thoth.ports.operation import OperationStorePort
from thoth.ports.runtime import ClockPort, IdGeneratorPort
from thoth.ports.store_bundle import StoreBundlePort
from thoth.ports.task_profile import TaskProfileCatalogPort
from thoth.protocol.bus import CommandBus


def assemble_runtime_entry(
    *,
    registration: RuntimeRegistration,
    artifact_ledger: ScopedArtifactLedger,
    clock: ClockPort,
    connector_service: ConnectorService,
    criterion_store: CriterionContractStorePort,
    effective_models: ModelResolverPort,
    evidence_graph_store: EvidenceGraphStorePort,
    field_measurement_service: FieldMeasurementService,
    full_memory_service: FullProjectMemoryService,
    ids: IdGeneratorPort,
    ledger: ManagedLedgerPort,
    mode: DeploymentMode,
    model_catalog: ModelCatalogPort | None,
    model_resolver: ModelResolverPort | None,
    operations: OperationStorePort,
    research_components: ResearchComponents,
    stores: StoreBundlePort,
    task_profiles: TaskProfileCatalogPort | None,
    thread_handlers: ThreadCommandHandlers,
    workspace: Path,
) -> RuntimeWithLedger[ManagedLedgerPort]:
    research_component = create_research_entry(
        registration.registry,
        stores,
        thread_handlers,
        artifact_ledger,
        criterion_store,
        connector_service,
        full_memory_service,
        evidence_graph_store,
        effective_models,
        clock,
        ids,
        task_profiles,
        model_catalog,
        model_resolver is None,
        research_components.tests,
        workspace=workspace,
        deployment_mode=mode,
    )
    bus: CommandBus | None = None
    try:
        before_continue = None
        if mode is DeploymentMode.HOSTED_REVIEW:
            from thoth.apps.hosted_review_dispatch import wait_for_hosted_dispatch

            before_continue = wait_for_hosted_dispatch
        bus = scope_bus(
            registration.registry,
            ledger,
            operations,
            clock,
            ids,
            stores.events,
            field_measurement_service,
            artifact_ledger.scopes,
            seal_abandoned_running=mode is DeploymentMode.HOSTED_REVIEW,
            before_continue=before_continue,
            queued_admission=research_component.entry.queued_admission,
        )
        registration.judgment_review.connect(bus, research_component.entry)
        from thoth.application.commands.research_queue_cancel import QueueAwareOperationCancel

        registration.registry.decorate(
            "operation/cancel",
            lambda original: (
                QueueAwareOperationCancel(
                    original, research_component.entry.records, research_component.entry.queue
                ).cancel
            ),
        )
        register_research_operations(registration.registry, research_component.entry)
        return RuntimeWithLedger(
            bus=bus, ledger=ledger, close_storage=research_component.close_storage
        )
    except BaseException as original:
        _release_failed_startup(bus, research_component, original)
        raise


def _release_failed_startup(
    bus: CommandBus | None, composition: ResearchEntryComposition, original: BaseException
) -> None:
    failures: list[tuple[str, BaseException]] = []
    if bus is not None:
        try:
            bus.close_tasks()
        except BaseException as error:
            failures.append(("bus_tasks", error))
    failures.extend(composition.release_after_failed_startup())
    for label, error in failures:
        # Provider/process exception messages can contain credentials or private paths.
        original.add_note(f"startup cleanup failed: {label}: {type(error).__name__}")
