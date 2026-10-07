"""Compose memory preparation/admission with the shared database and current auth store."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from thoth.adapters.memory import (
    DeterministicRoleMemoryReviewer,
    KeywordMemoryReranker,
    LocalMemoryEmbedding,
    LocalRelationProjectionBuilder,
)
from thoth.application.commands.memory_edits import MemoryEditHandlers
from thoth.application.commands.memory_full import MemoryFullHandlers
from thoth.application.commands.memory_revisions import MemoryRevisionHandlers
from thoth.application.commands.memory_settings import MemorySettingsHandlers
from thoth.application.services.full_project_memory import FullProjectMemoryService
from thoth.application.services.memory_admission import MemoryAdmissionService
from thoth.application.services.memory_edit_service import MemoryEditService
from thoth.application.services.memory_settings import MemorySettingsService
from thoth.application.services.post_execution_memory import PostExecutionMemory
from thoth.application.services.request_records import RequestRecords
from thoth.application.services.research_freshness import ResearchFreshnessService
from thoth.application.services.revision_service import RevisionCommitService
from thoth.application.services.scoped_memory import MemoryResourceAccess, ScopedFullMemoryStore
from thoth.application.services.scoped_memory_controls import (
    MemoryControlWriter,
    ScopedMemoryControls,
)
from thoth.ports.governance import GovernanceStorePort
from thoth.ports.ledger import LedgerPort, ManagedLedgerPort
from thoth.ports.memory import FullMemoryStorePort, MemoryReviewerPort, MemoryStorePort
from thoth.ports.project import ProjectStorePort
from thoth.ports.resource_scope import ResourceAccessPort
from thoth.ports.runtime import ClockPort, IdGeneratorPort
from thoth.ports.store_bundle import StoreBundlePort
from thoth.protocol.registry import MethodRegistry


def create_post_execution_memory(
    stores: StoreBundlePort,
    memory: FullProjectMemoryService,
    clock: ClockPort,
    ids: IdGeneratorPort,
) -> PostExecutionMemory:
    return PostExecutionMemory(RequestRecords(stores.ledger, stores.controls, clock, ids), memory)


@dataclass(frozen=True)
class MemoryHandlers:
    controls: MemoryFullHandlers
    revisions: MemoryRevisionHandlers
    edits: MemoryEditHandlers
    settings: MemorySettingsHandlers


def create_memory_settings(
    stores: StoreBundlePort, clock: ClockPort, ids: IdGeneratorPort
) -> MemorySettingsService:
    return MemorySettingsService(RequestRecords(stores.ledger, stores.controls, clock, ids))


def create_memory_handlers(
    stores: StoreBundlePort,
    legacy: MemoryStorePort,
    full: FullMemoryStorePort,
    ledger: LedgerPort,
    clock: ClockPort,
    ids: IdGeneratorPort,
    access: ResourceAccessPort,
    memory: FullProjectMemoryService,
) -> MemoryHandlers:
    """Build every memory RPC owner and seed the two built-in memory policies."""

    visible = ScopedMemoryControls(stores.controls, access, ledger)
    controls = MemoryFullHandlers(
        records=visible,
        controls=MemoryControlWriter(store=visible, clock=clock, ids=ids),
        legacy=legacy,
        full=full,
        ledger=ledger,
    )
    controls.seed_policies()
    freshness = ResearchFreshnessService(ledger)
    revisions = MemoryRevisionHandlers(
        full=full,
        ledger=ledger,
        owner_state=lambda project_id, owner: freshness.owner_eligibility(project_id, owner).state,
    )
    service = MemoryEditService(
        memory=memory,
        full=full,
        ledger=ledger,
        commits=RevisionCommitService(ledger, clock, ids, policy_version="memory-edit:1.0.0"),
        clock=clock,
        ids=ids,
    )
    return MemoryHandlers(
        controls,
        revisions,
        MemoryEditHandlers(service=service, rows=revisions),
        MemorySettingsHandlers(
            service=create_memory_settings(stores, clock, ids), projects=stores.projects
        ),
    )


def create_memory_components(
    *,
    ledger: ManagedLedgerPort,
    stores: StoreBundlePort,
    candidates: MemoryStorePort,
    projects: ProjectStorePort,
    governance: GovernanceStorePort,
    clock: ClockPort,
    ids: IdGeneratorPort,
    reviewer: MemoryReviewerPort | None,
    fault_injector: Callable[[str], None] | None,
    resource_access: ResourceAccessPort | None = None,
) -> tuple[FullMemoryStorePort, FullProjectMemoryService]:
    store = stores.full_memory(fault_injector)
    access = None if resource_access is None else MemoryResourceAccess(store, resource_access)
    settings = create_memory_settings(stores, clock, ids)
    service = FullProjectMemoryService(
        store=store,
        candidates=candidates,
        ledger=ledger,
        clock=clock,
        ids=ids,
        reviewer=reviewer or DeterministicRoleMemoryReviewer(),
        embedding=LocalMemoryEmbedding(),
        reranker=KeywordMemoryReranker(),
        projection_builder=LocalRelationProjectionBuilder(),
        admission=MemoryAdmissionService(
            projects=projects,
            governance=governance,
            sessions=stores.auth,
            clock=clock,
        ),
        resource_access=access,
        injection=settings,
        expansion_switch=settings,
    )
    return (store if access is None else ScopedFullMemoryStore(store, access)), service


def register_memory_handlers(registry: MethodRegistry, owners: MemoryHandlers) -> None:
    handlers = owners.controls
    registry.register("memory/revision/list", owners.revisions.revision_list)
    registry.register("memory/edit/propose", owners.edits.propose)
    registry.register("memory/settings/read", owners.settings.read)
    registry.register("memory/settings/update", owners.settings.update)
    registry.register("memory/list", handlers.list)
    registry.register("memory/read", handlers.read)
    registry.register("memory/candidate/list", handlers.candidate_list)
    registry.register("memory/candidate/read", handlers.candidate_read)
    registry.register("memory/context/read", handlers.context_read)
    registry.register("memory/policy/list", handlers.policy_list)
    registry.register("memory/policy/read", handlers.policy_read)
    registry.register("memory/gate/read", handlers.gate_read)
    registry.register("memory/conflict/list", handlers.conflict_list)
    registry.register("memory/recall/audit", handlers.recall_audit)
    registry.register("memory/projection/status", handlers.projection_status)
    registry.register("memory/candidate/create", handlers.candidate_create)
    registry.register("memory/candidate/classify", handlers.candidate_classify)
    registry.register("memory/validate", handlers.validate)
    registry.register("memory/changeSet/propose", handlers.change_set_propose)
    registry.register("memory/revalidate", handlers.revalidate)
    registry.register("memory/retire/propose", handlers.retire_propose)
    registry.register("memory/context/build", handlers.context_build)
    registry.register("memory/projection/rebuild", handlers.projection_rebuild)
    registry.register("memory/retention/evaluate", handlers.retention_evaluate)
