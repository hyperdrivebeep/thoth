"""Compose memory preparation/admission with the shared database and current auth store."""

from __future__ import annotations

from collections.abc import Callable

from thoth.adapters.memory import (
    DeterministicRoleMemoryReviewer,
    KeywordMemoryReranker,
    LocalMemoryEmbedding,
    LocalRelationProjectionBuilder,
)
from thoth.application.commands.memory_full import MemoryFullHandlers
from thoth.application.services.full_project_memory import FullProjectMemoryService
from thoth.application.services.memory_admission import MemoryAdmissionService
from thoth.application.services.post_execution_memory import PostExecutionMemory
from thoth.application.services.request_records import RequestRecords
from thoth.application.services.scoped_memory import MemoryResourceAccess, ScopedFullMemoryStore
from thoth.application.services.scoped_memory_controls import (
    MemoryControlWriter,
    ScopedMemoryControls,
)
from thoth.ports.control_record import ControlRecordStorePort
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


def create_memory_handlers(
    records: ControlRecordStorePort,
    legacy: MemoryStorePort,
    full: FullMemoryStorePort,
    ledger: LedgerPort,
    clock: ClockPort,
    ids: IdGeneratorPort,
    access: ResourceAccessPort,
) -> MemoryFullHandlers:
    visible = ScopedMemoryControls(records, access, ledger)
    return MemoryFullHandlers(
        records=visible,
        controls=MemoryControlWriter(store=visible, clock=clock, ids=ids),
        legacy=legacy,
        full=full,
        ledger=ledger,
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
    )
    return (store if access is None else ScopedFullMemoryStore(store, access)), service


def register_memory_handlers(registry: MethodRegistry, handlers: MemoryFullHandlers) -> None:
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
