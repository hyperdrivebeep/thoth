"""Concrete local application composition root."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Unpack, overload

from thoth.adapters.connectors import ConnectorRegistry, GitReadConnector, LocalFileConnector
from thoth.adapters.connectors.project_public_web import ProjectPublicWebConnector
from thoth.adapters.evaluators import default_evaluator_registry
from thoth.adapters.projectpacks import FilesystemProjectPackLoader
from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.sandbox import DisabledSandboxAdapter
from thoth.adapters.storage.workspace_setup import read_setup
from thoth.application.commands import (
    ActionHandlers,
    CriterionFullHandlers,
    DecisionObjectHandlers,
    EvidenceCommandHandlers,
    ExecutionHandlers,
    FieldMeasurementHandlers,
    HypothesisHandlers,
    InvestigationQueryHandlers,
    OutcomeHandlers,
    ProjectCommandHandlers,
    ProjectionQueryHandlers,
    ProjectPackCommandHandlers,
    ThreadCommandHandlers,
)
from thoth.application.commands.research_operations import register_research_operations
from thoth.application.services import (
    AcquisitionCoordinator,
    ConnectorService,
    ControlRecordService,
    CriticalCounterSearchCoordinator,
    DecisionObjectService,
    InvestigationService,
    OutcomeService,
    PolicyGate,
    RegisteredSandboxRouter,
    RevisionCommitService,
    SandboxService,
)
from thoth.apps.behavior_composition import create_behavior_runtime
from thoth.apps.criteria_composition import create_criterion_service
from thoth.apps.criterion_object_routes import register_criterion_and_object_methods
from thoth.apps.decision_chain_routes import register_decision_chain_methods
from thoth.apps.evidence_composition import create_evidence_components
from thoth.apps.export_composition import create_export_handlers, create_lifecycle_handlers
from thoth.apps.improvement_composition import (
    create_improvement_components,
    register_improvement_handlers,
)
from thoth.apps.judgment_review_composition import install_judgment_review
from thoth.apps.lifecycle_routes import register_closure_and_export_methods
from thoth.apps.management_routes import (
    register_investigation_methods,
    register_project_methods,
    register_thread_methods,
)
from thoth.apps.memory_composition import (
    create_memory_components,
    create_memory_handlers,
    register_memory_handlers,
)
from thoth.apps.model_composition import create_models, wrap_research_models
from thoth.apps.outcome_receipt_routes import register_outcome_and_receipt_methods
from thoth.apps.projectpack_execution import ProjectPackExecutionFactory
from thoth.apps.reference_composition import reference_thread_entry
from thoth.apps.research_entry_composition import create_research_entry
from thoth.apps.research_runtime import create_research_components
from thoth.apps.resource_scope_composition import (
    create_evidence_access,
    create_execution_access,
    create_memory_access,
    create_resources,
    register_resource_handlers,
    scope_bus,
    source_policy_defaults,
)
from thoth.apps.revision_composition import create_revision_handlers, register_revision_methods
from thoth.apps.runtime_types import AppRuntime, RuntimeOptions, RuntimeWithLedger
from thoth.apps.source_composition import (
    create_ingestion_service,
    create_source_handlers,
    create_source_time_service,
)
from thoth.apps.source_evidence_routes import register_source_and_evidence_methods
from thoth.apps.storage_composition import create_shared_storage_services, open_stores
from thoth.apps.test_runtime import create_execution_components
from thoth.apps.thread_analysis_composition import create_thread_analysis
from thoth.domain.deployment_mode import DeploymentMode, parse_deployment_mode
from thoth.domain.environment import EnvironmentProfile
from thoth.domain.policy import preferred_hosts_from_payload
from thoth.domain.public_web_access import PROJECT_PUBLIC_WEB_CONNECTOR_ID
from thoth.domain.resource_scope import ResourceScopePolicy
from thoth.ports.evaluation_runner import EvaluationCatalogPort
from thoth.ports.improvement import ImprovementEvaluatorPort
from thoth.ports.ledger import ManagedLedgerPort
from thoth.ports.memory import MemoryReviewerPort
from thoth.ports.model import ModelResolverPort
from thoth.ports.model_catalog import ModelCatalogPort
from thoth.ports.parser import ParserRegistryPort
from thoth.ports.sandbox import SandboxPort
from thoth.ports.store_bundle import StoreFactoryPort
from thoth.ports.task_profile import TaskProfileCatalogPort
from thoth.protocol.registry import MethodRegistry

if TYPE_CHECKING:

    @overload
    def create_runtime(
        workspace: Path, *, storage_factory: None = None, **options: Unpack[RuntimeOptions]
    ) -> AppRuntime: ...

    @overload
    def create_runtime(
        workspace: Path, *, storage_factory: StoreFactoryPort, **options: Unpack[RuntimeOptions]
    ) -> RuntimeWithLedger[ManagedLedgerPort]: ...


def create_runtime(
    workspace: Path,
    *,
    projectpack_root: Path | None = None,
    connector_registry: ConnectorRegistry | None = None,
    sandbox_adapter: SandboxPort | None = None,
    environment_profile: EnvironmentProfile | None = None,
    acquisition_fault_injector: Callable[[str], None] | None = None,
    evidence_fault_injector: Callable[[str], None] | None = None,
    memory_fault_injector: Callable[[str], None] | None = None,
    improvement_evaluator: ImprovementEvaluatorPort | None = None,
    evaluation_catalog: EvaluationCatalogPort | None = None,
    measurement_secret: bytes | None = None,
    model_resolver: ModelResolverPort | None = None,
    parser_registry: ParserRegistryPort | None = None,
    memory_reviewer: MemoryReviewerPort | None = None,
    resource_scope_policy: ResourceScopePolicy | None = None,
    storage_factory: StoreFactoryPort | None = None,
    task_profiles: TaskProfileCatalogPort | None = None,
    model_catalog: ModelCatalogPort | None = None,
    deployment_mode: DeploymentMode | None = None,
) -> RuntimeWithLedger[ManagedLedgerPort]:
    mode = deployment_mode or parse_deployment_mode()
    if mode is DeploymentMode.HOSTED_REVIEW:
        from thoth.apps.hosted_review_composition import (
            hosted_openai_resolver,
            hosted_review_catalog,
        )

        if model_catalog is None:
            model_catalog = hosted_review_catalog()
        if model_resolver is None:
            model_resolver = hosted_openai_resolver()
    stores = open_stores(workspace, storage_factory)
    ledger = stores.ledger
    try:
        clock = SystemClock()
        ids = UuidIdGenerator()
        operations = stores.operations
        projects = stores.projects
        if mode is DeploymentMode.HOSTED_REVIEW:
            from thoth.apps.hosted_review_composition import fail_stale_running_operations

            fail_stale_running_operations(operations, projects, controls=stores.controls)
        governance = stores.governance
        artifact_ledger = create_resources(ledger, stores, projects, governance, clock, ids)
        evidence_graph_store = create_evidence_access(ledger, stores, artifact_ledger.scopes)
        criterion_store = stores.criteria(artifact_ledger.scopes)
        control_store = stores.controls
        receipt_dag_store = stores.receipt_dag
        shared = create_shared_storage_services(stores, clock, ids, measurement_secret)
        receipt_dag_service = shared.receipts
        decision_object_store = stores.decision_objects(artifact_ledger.scopes)
        thread_store = stores.threads
        thread_runtime_store = stores.thread_runtime
        investigation_store = stores.investigations(artifact_ledger.scopes)
        acquisition_trace_store = stores.acquisition_trace
        memory_store = create_memory_access(ledger, stores, artifact_ledger.scopes)
        baseline_service = shared.baselines
        behavior_runtime = create_behavior_runtime(ledger, stores, control_store, clock, ids)
        behavior_store, behavior_candidates, improvement_runner = behavior_runtime.legacy()
        full_memory_store, full_memory_service = create_memory_components(
            stores=stores,
            ledger=ledger,
            resource_access=artifact_ledger.scopes,
            candidates=memory_store,
            projects=projects,
            governance=governance,
            clock=clock,
            ids=ids,
            reviewer=memory_reviewer,
            fault_injector=memory_fault_injector,
        )
        dependency_graph = stores.dependencies
        execution_store = create_execution_access(ledger, stores, artifact_ledger.scopes)
        outcome_store = stores.outcomes(artifact_ledger.scopes)
        field_measurement_service = shared.measurement
        object_store = stores.objects
        research_components = create_research_components(
            stores=stores,
            ledger=ledger,
            objects=decision_object_store,
            artifacts=artifact_ledger,
            governance=governance,
            executions=execution_store,
            blobs=object_store,
            projects=projects,
            clock=clock,
            ids=ids,
        )
        hypothesis_store, hypothesis_service = (
            research_components.hypotheses,
            research_components.hypothesis_service,
        )
        action_store, action_service = (
            research_components.actions,
            research_components.action_service,
        )
        research_identity = research_components.identity
        investigation_service = InvestigationService(
            store=investigation_store,
            clock=clock,
            ids=ids,
        )
        evidence_unit_of_work, evidence_graph_service = create_evidence_components(
            stores=stores,
            ledger=ledger,
            store=evidence_graph_store,
            artifacts=artifact_ledger,
            clock=clock,
            ids=ids,
            fault_injector=evidence_fault_injector,
        )
        source_time_service = create_source_time_service(
            stores, artifact_ledger, evidence_graph_service, clock, ids
        )
        ingestion_service = create_ingestion_service(
            stores, artifact_ledger, clock, ids, parser_registry, source_time_service
        )
        criterion_service = create_criterion_service(
            criterion_store, artifact_ledger, ledger, clock, ids
        )
        control_service = ControlRecordService(store=control_store, clock=clock, ids=ids)
        recursive_improvement, improvement_handlers, evaluation_handlers = (
            create_improvement_components(
                stores=stores,
                records=control_store,
                controls=control_service,
                evaluator=default_evaluator_registry(improvement_evaluator),
                ledger=ledger,
                projects=projects,
                governance=governance,
                behaviors=behavior_store,
                clock=clock,
                ids=ids,
                baselines=baseline_service,
                behavior_candidates=behavior_candidates,
                improvement_runner=improvement_runner,
                evaluation_catalog=evaluation_catalog,
                behavior_runtime=behavior_runtime,
                resource_access=artifact_ledger.scopes,
                objects=object_store,
            )
        )

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

        def workspace_setup():
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
        effective_models = wrap_research_models(
            behavior_runtime, create_models(model_resolver, artifact_ledger.scopes, workspace)
        )
        effective_sandbox = sandbox_adapter or DisabledSandboxAdapter()
        sandbox_router = RegisteredSandboxRouter()
        sandbox_capability = effective_sandbox.capability
        sandbox_router.register(
            sandbox_capability.runtime_profile,
            effective_sandbox,
            available=sandbox_capability.available,
            version=sandbox_capability.runtime_version,
            security_tier=sandbox_capability.security_tier,
            network_modes=sandbox_capability.network_modes,
        )
        sandbox_service = SandboxService(
            router=sandbox_router,
            artifacts=artifact_ledger,
            objects=object_store,
            controls=control_service,
            ingestion=ingestion_service,
            projects=projects,
            policies=PolicyGate(policies=governance, clock=clock),
            ledger=ledger,
            clock=clock,
            ids=ids,
        )
        execution_service, r2_closed_loop = create_execution_components(
            stores=stores,
            research=research_components,
            actions=action_store,
            action_service=action_service,
            executions=execution_store,
            artifacts=artifact_ledger,
            sandbox=sandbox_service,
            ledger=ledger,
            policies=governance,
            clock=clock,
            ids=ids,
        )
        decision_object_service = DecisionObjectService(
            store=decision_object_store,
            artifacts=artifact_ledger,
            dependencies=dependency_graph,
            ledger=ledger,
            commits=RevisionCommitService(
                ledger,
                clock,
                ids,
                policy_version="decision-object:2.0.0",
            ),
            clock=clock,
            ids=ids,
        )
        decision_object_service.seed_profiles()
        outcome_service = OutcomeService(
            store=outcome_store,
            objects=decision_object_store,
            actions=action_store,
            executions=execution_store,
            artifacts=artifact_ledger,
            ledger=ledger,
            commits=RevisionCommitService(
                ledger,
                clock,
                ids,
                policy_version="outcome:2.0.0",
            ),
            clock=clock,
            ids=ids,
            baselines=baseline_service,
        )
        outcome_service.seed_profiles()
        project_handlers = ProjectCommandHandlers(
            store=projects,
            governance=governance,
            artifacts=artifact_ledger,
            ledger=ledger,
            clock=clock,
            ids=ids,
            default_policy_payload=source_policy_defaults(
                effective_connectors, environment_profile, sandbox_adapter, resource_scope_policy
            ),
            workspace_setup=workspace_setup,
            public_web_registered=lambda: any(
                item.connector_id == PROJECT_PUBLIC_WEB_CONNECTOR_ID
                for item in effective_connectors.capabilities()
            ),
        )
        evidence_handlers = EvidenceCommandHandlers(
            store=evidence_graph_store,
            service=evidence_graph_service,
            artifacts=artifact_ledger,
        )
        criterion_handlers = CriterionFullHandlers(
            store=criterion_store,
            service=criterion_service,
        )
        object_handlers = DecisionObjectHandlers(
            store=decision_object_store,
            service=decision_object_service,
            dependencies=dependency_graph,
            threads=thread_store,
        )
        hypothesis_handlers = HypothesisHandlers(
            store=hypothesis_store,
            service=hypothesis_service,
            objects=decision_object_store,
            artifacts=artifact_ledger,
            threads=thread_store,
            investigations=investigation_service,
        )
        action_handlers = ActionHandlers(
            store=action_store,
            service=action_service,
            objects=decision_object_store,
            operations=operations,
        )
        execution_handlers = ExecutionHandlers(
            store=execution_store,
            actions=action_store,
            service=execution_service,
            sandbox=sandbox_service,
            ledger=ledger,
        )
        outcome_handlers = OutcomeHandlers(store=outcome_store, service=outcome_service)
        export_handlers = create_export_handlers(
            stores=stores,
            workspace=workspace,
            records=control_store,
            controls=control_service,
            ledger=ledger,
            artifacts=artifact_ledger,
            evidence=evidence_graph_store,
            baselines=baseline_service,
        )
        thread_handlers = ThreadCommandHandlers(
            projects=projects,
            threads=thread_store,
            runtime=thread_runtime_store,
            ledger=ledger,
            clock=clock,
            ids=ids,
            investigation_service=investigation_service,
            investigations=investigation_store,
            decision_objects=decision_object_service,
        )
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
        thread_analysis_handlers = create_thread_analysis(
            stores,
            artifact_ledger,
            memory_store,
            criterion_store,
            criterion_service,
            effective_models,
            acquisition_coordinator,
            critical_counter_search,
            r2_closed_loop,
            receipt_dag_service,
            full_memory_service,
            recursive_improvement,
            baseline_service,
            research_identity,
            clock,
            ids,
        )
        revision_handlers, revision_full_handlers = create_revision_handlers(
            ledger,
            stores,
            control_store,
            control_service,
            dependency_graph,
            governance,
            clock,
            ids,
            baseline_service,
            artifact_ledger.scopes,
        )
        receipt_handlers, lifecycle_handlers, closure_handlers = create_lifecycle_handlers(
            stores=stores,
            ledger=ledger,
            records=control_store,
            controls=control_service,
            artifacts=artifact_ledger,
            clock=clock,
            ids=ids,
            dag=receipt_dag_service,
            dag_store=receipt_dag_store,
            actions=action_store,
            executions=execution_store,
            outcomes=outcome_store,
            baselines=baseline_service,
        )
        projection_handlers = ProjectionQueryHandlers(
            ledger=ledger,
            artifacts=artifact_ledger,
            clock=clock,
            research=research_identity,
        )
        investigation_handlers = InvestigationQueryHandlers(
            store=investigation_store,
            service=investigation_service,
            threads=thread_store,
            acquisition=acquisition_trace_store,
        )
        memory_full_handlers = create_memory_handlers(
            stores,
            memory_store,
            full_memory_store,
            ledger,
            clock,
            ids,
            artifact_ledger.scopes,
            full_memory_service,
        )
        field_measurement_handlers = FieldMeasurementHandlers(field_measurement_service)
        registry = MethodRegistry()
        register_resource_handlers(registry, artifact_ledger.scopes)
        registry.register("field/protocol/seal", field_measurement_handlers.protocol_seal)
        registry.register("field/session/start", field_measurement_handlers.session_start)
        registry.register("field/event/record", field_measurement_handlers.event_record)
        registry.register("field/session/end", field_measurement_handlers.session_end)
        registry.register("field/score/record", field_measurement_handlers.score_record)
        registry.register("field/export/build", field_measurement_handlers.export_build)
        registry.register("projectpack/list", projectpack_handlers.list)
        registry.register("projectpack/run", projectpack_handlers.run)
        register_project_methods(registry, project_handlers)
        register_closure_and_export_methods(
            registry, lifecycle_handlers, closure_handlers, export_handlers
        )
        register_source_and_evidence_methods(registry, source_handlers, evidence_handlers)
        register_thread_methods(registry, thread_handlers)
        behavior_runtime.register_thread(
            registry,
            reference_thread_entry(
                thread_analysis_handlers,
                criteria=criterion_store,
                service=criterion_service,
                artifacts=artifact_ledger,
                ledger=ledger,
                threads=thread_store,
                clock=clock,
                ids=ids,
                models=effective_models,
                projects=projects,
            ),
        )
        register_revision_methods(
            registry,
            revision_handlers,
            revision_full_handlers,
            stores,
            artifact_ledger.scopes,
            baseline_service,
            clock,
            ids,
        )
        register_decision_chain_methods(
            registry, hypothesis_handlers, action_handlers, execution_handlers, projection_handlers
        )
        judgment_review = install_judgment_review(
            registry, stores, control_service, hypothesis_store, clock, ids
        )
        register_criterion_and_object_methods(registry, criterion_handlers, object_handlers)
        register_memory_handlers(registry, memory_full_handlers)
        register_improvement_handlers(registry, improvement_handlers, evaluation_handlers)
        register_outcome_and_receipt_methods(registry, outcome_handlers, receipt_handlers)
        register_investigation_methods(registry, investigation_handlers)
        research_component = create_research_entry(
            registry,
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
        before_continue = None
        if mode is DeploymentMode.HOSTED_REVIEW:
            from thoth.apps.hosted_review_dispatch import wait_for_hosted_dispatch

            before_continue = wait_for_hosted_dispatch
        bus = scope_bus(
            registry,
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
        judgment_review.connect(bus, research_component.entry)
        from thoth.application.commands.research_queue_cancel import QueueAwareOperationCancel

        registry.decorate(
            "operation/cancel",
            lambda original: (
                QueueAwareOperationCancel(
                    original, research_component.entry.records, research_component.entry.queue
                ).cancel
            ),
        )

        register_research_operations(registry, research_component.entry)
        return RuntimeWithLedger(
            bus=bus, ledger=ledger, close_storage=research_component.close_storage
        )
    except BaseException:
        stores.close()
        raise
