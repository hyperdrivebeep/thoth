"""Compose the shared local Improvement owner and its public command surface."""

from __future__ import annotations

from thoth.adapters.behavior.registry import default_behavior_components
from thoth.adapters.evaluators.execution_registry import default_execution_registry
from thoth.adapters.evaluators.scoring import FrozenPairScorer
from thoth.application.commands.evaluation_runs import EvaluationRunHandlers
from thoth.application.commands.improvement_full import ImprovementHandlers
from thoth.application.services.baseline_service import BaselineService
from thoth.application.services.behavior_candidate_service import BehaviorCandidateService
from thoth.application.services.control_record_service import ControlRecordService
from thoth.application.services.evaluation_inputs import EvaluationInputResolver
from thoth.application.services.evaluation_producer import EvaluationProducer
from thoth.application.services.improvement_candidate_generator import (
    BoundedBehaviorCandidateGenerator,
)
from thoth.application.services.improvement_evaluation_gate import ImprovementEvaluationGate
from thoth.application.services.improvement_exposure_service import ImprovementExposureService
from thoth.application.services.improvement_runner import ImprovementRunner
from thoth.application.services.improvement_thread_scope import ImprovementThreadScope
from thoth.application.services.paired_evaluation_service import PairedEvaluationService
from thoth.application.services.recursive_improvement import RecursiveImprovementCoordinator
from thoth.apps.behavior_composition import BehaviorRuntime
from thoth.ports.behavior_artifact import BehaviorArtifactStorePort
from thoth.ports.control_record import ControlRecordStorePort
from thoth.ports.evaluation_runner import EvaluationCatalogPort
from thoth.ports.governance import GovernanceStorePort
from thoth.ports.improvement import ImprovementEvaluatorPort
from thoth.ports.ledger import ManagedLedgerPort
from thoth.ports.object_store import ObjectStorePort
from thoth.ports.project import ProjectStorePort
from thoth.ports.resource_scope import ResourceAccessPort
from thoth.ports.runtime import ClockPort, IdGeneratorPort
from thoth.ports.store_bundle import StoreBundlePort
from thoth.protocol.registry import MethodRegistry


def create_improvement_components(
    *,
    records: ControlRecordStorePort,
    controls: ControlRecordService,
    evaluator: ImprovementEvaluatorPort,
    ledger: ManagedLedgerPort,
    stores: StoreBundlePort,
    projects: ProjectStorePort,
    governance: GovernanceStorePort,
    behaviors: BehaviorArtifactStorePort,
    clock: ClockPort,
    ids: IdGeneratorPort,
    baselines: BaselineService,
    behavior_candidates: BehaviorCandidateService,
    improvement_runner: ImprovementRunner,
    evaluation_catalog: EvaluationCatalogPort | None,
    resource_access: ResourceAccessPort,
    objects: ObjectStorePort,
    behavior_runtime: BehaviorRuntime,
) -> tuple[RecursiveImprovementCoordinator, ImprovementHandlers, EvaluationRunHandlers]:
    thread_scope = ImprovementThreadScope(records, stores.threads)
    gate = ImprovementEvaluationGate(
        ledger=ledger,
        projects=projects,
        governance=governance,
        behaviors=behaviors,
        baselines=baselines,
        records=records,
        controls=controls,
        sessions=stores.auth,
        clock=clock,
    )
    paired = (
        None
        if evaluation_catalog is None
        else PairedEvaluationService(
            inputs=EvaluationInputResolver(
                behaviors=behaviors,
                records=records,
                catalog=evaluation_catalog,
                gate=gate,
                access=resource_access,
                clock=clock,
                ids=ids,
                thread_scope=thread_scope,
                behavior_components=default_behavior_components(),
                behavior_snapshots=behavior_runtime.snapshots,
            ),
            store=stores.evaluation_runs,
            executors=default_execution_registry(objects, clock, ids),
            scorer=FrozenPairScorer(objects),
            ledger=ledger,
            objects=objects,
        )
    )
    coordinator = RecursiveImprovementCoordinator(
        records=records,
        controls=controls,
        evaluator=evaluator,
        unit_of_work=stores.atomic_uow,
        ledger=ledger,
        evaluation_gate=gate,
        clock=clock,
        ids=ids,
        baselines=baselines,
        behavior_candidates=behavior_candidates,
        improvement_runner=improvement_runner,
        producer=None if paired is None else EvaluationProducer(records, paired),
        candidate_generator=None
        if evaluation_catalog is None
        else BoundedBehaviorCandidateGenerator(
            records=records,
            controls=controls,
            behaviors=behaviors,
            snapshots=behavior_runtime.snapshots,
            components=default_behavior_components(),
            catalog=evaluation_catalog,
            thread_scope=thread_scope,
            clock=clock,
            ids=ids,
        ),
    )
    exposures = ImprovementExposureService(
        store=stores.behavior_executions,
        behaviors=behaviors,
        candidates=behavior_candidates,
        components=default_behavior_components(),
        records=records,
        controls=controls,
        paired=paired,
        governance=governance,
        sessions=stores.auth,
        threads=stores.threads,
        thread_scope=thread_scope,
        ledger=ledger,
        clock=clock,
        ids=ids,
    )
    behavior_runtime.connect_exposures(exposures)
    handlers = ImprovementHandlers(
        records=records,
        controls=controls,
        governance=governance,
        ledger=ledger,
        paired=paired,
        thread_scope=thread_scope,
        exposures=exposures,
    )
    return coordinator, handlers, EvaluationRunHandlers(records, paired, thread_scope)


def register_improvement_handlers(
    registry: MethodRegistry, handlers: ImprovementHandlers, evaluations: EvaluationRunHandlers
) -> None:
    registry.register("improvement/list", handlers.list)
    registry.register("improvement/read", handlers.read)
    registry.register("improvement/evaluation/read", handlers.evaluation_read)
    registry.register("improvement/exposure/read", handlers.exposure_read)
    registry.register("improvement/shadow/read", handlers.shadow_read)
    registry.register("improvement/canary/read", handlers.canary_read)
    registry.register("improvement/promotion/read", handlers.promotion_read)
    registry.register("improvement/audit/read", handlers.audit_read)
    registry.register("improvement/propose", handlers.propose)
    registry.register("improvement/revise", handlers.revise)
    registry.register("improvement/evaluation/plan", handlers.evaluation_plan)
    registry.register("improvement/evaluation/assess", handlers.evaluation_assess)
    registry.register("improvement/exposure/prepare", handlers.exposure_prepare)
    registry.register("improvement/promotion/prepare", handlers.promotion_prepare)
    registry.register("improvement/promotion/decide", handlers.promotion_decide)
    registry.register("improvement/rollback/prepare", handlers.rollback_prepare)
    registry.register("improvement/retire/propose", handlers.retire_propose)
    registry.register("improvement/evaluation/run", evaluations.run)
    registry.register("improvement/evaluation/result/read", evaluations.read)
    if handlers.exposure_commands is not None:
        registry.register("improvement/exposure/runtime/read", handlers.exposure_commands.read)
        registry.register("improvement/exposure/start", handlers.exposure_commands.start)
        registry.register("improvement/exposure/decide", handlers.exposure_commands.decide)
        registry.register("improvement/exposure/complete", handlers.exposure_commands.complete)
        registry.register("improvement/exposure/rollback", handlers.exposure_commands.rollback)
