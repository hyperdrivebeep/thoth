"""Compose analysis and post-execution learning against the same owned stores."""

from thoth.application.commands.thread_analysis import ThreadAnalysisCommandHandlers
from thoth.application.services import (
    AcquisitionCoordinator,
    BaselineService,
    CriterionContractService,
    CriticalCounterSearchCoordinator,
    FullProjectMemoryService,
    R2ClosedLoopCoordinator,
    ReceiptDagService,
    RecursiveImprovementCoordinator,
    SourceGroundedCriterionProfileRouter,
)
from thoth.application.services.research_identity_service import ResearchIdentityService
from thoth.apps.memory_composition import create_post_execution_memory
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.criterion_contract import CriterionContractStorePort
from thoth.ports.memory import MemoryStorePort
from thoth.ports.model import ModelResolverPort
from thoth.ports.runtime import ClockPort, IdGeneratorPort
from thoth.ports.store_bundle import StoreBundlePort


def create_thread_analysis(
    stores: StoreBundlePort,
    artifacts: ArtifactLedgerPort,
    memory: MemoryStorePort,
    criteria: CriterionContractStorePort,
    criterion_service: CriterionContractService,
    models: ModelResolverPort,
    acquisition: AcquisitionCoordinator,
    critical_counter_search: CriticalCounterSearchCoordinator,
    r2_closed_loop: R2ClosedLoopCoordinator,
    receipt_dag: ReceiptDagService,
    full_memory: FullProjectMemoryService,
    recursive_improvement: RecursiveImprovementCoordinator,
    baselines: BaselineService,
    research: ResearchIdentityService,
    clock: ClockPort,
    ids: IdGeneratorPort,
) -> ThreadAnalysisCommandHandlers:
    return ThreadAnalysisCommandHandlers(
        projects=stores.projects,
        threads=stores.threads,
        artifacts=artifacts,
        ledger=stores.ledger,
        memory=memory,
        clock=clock,
        ids=ids,
        dependencies=stores.dependencies,
        governance=stores.governance,
        thread_runtime=stores.thread_runtime,
        criteria=criteria,
        criterion_service=criterion_service,
        models=models,
        acquisition=acquisition,
        critical_counter_search=critical_counter_search,
        r2_closed_loop=r2_closed_loop,
        receipt_dag=receipt_dag,
        full_memory=full_memory,
        unit_of_work=stores.atomic_uow,
        recursive_improvement=recursive_improvement,
        criterion_profiles=SourceGroundedCriterionProfileRouter(
            profiles=criteria, clock=clock, ids=ids, general_profile_ref="GENERAL_RND"
        ),
        baselines=baselines,
        research=research,
        post_execution_memory=create_post_execution_memory(stores, full_memory, clock, ids),
    )
