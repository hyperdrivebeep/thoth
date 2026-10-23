"""Concrete local application composition root."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Unpack, overload

from thoth.adapters.connectors import ConnectorRegistry
from thoth.adapters.connectors import GitReadConnector as GitReadConnector
from thoth.adapters.connectors import LocalFileConnector as LocalFileConnector
from thoth.adapters.connectors.project_public_web import (
    ProjectPublicWebConnector as ProjectPublicWebConnector,
)
from thoth.adapters.evaluators import default_evaluator_registry
from thoth.adapters.projectpacks import FilesystemProjectPackLoader as FilesystemProjectPackLoader
from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.sandbox import DisabledSandboxAdapter as DisabledSandboxAdapter
from thoth.adapters.storage.workspace_setup import read_setup as read_setup
from thoth.application.commands import (
    ActionHandlers as ActionHandlers,
)
from thoth.application.commands import (
    CriterionFullHandlers as CriterionFullHandlers,
)
from thoth.application.commands import (
    DecisionObjectHandlers as DecisionObjectHandlers,
)
from thoth.application.commands import (
    EvidenceCommandHandlers as EvidenceCommandHandlers,
)
from thoth.application.commands import (
    ExecutionHandlers as ExecutionHandlers,
)
from thoth.application.commands import FieldMeasurementHandlers, ThreadCommandHandlers
from thoth.application.commands import (
    HypothesisHandlers as HypothesisHandlers,
)
from thoth.application.commands import InvestigationQueryHandlers as InvestigationQueryHandlers
from thoth.application.commands import (
    OutcomeHandlers as OutcomeHandlers,
)
from thoth.application.commands import (
    ProjectCommandHandlers as ProjectCommandHandlers,
)
from thoth.application.commands import ProjectionQueryHandlers as ProjectionQueryHandlers
from thoth.application.commands import ProjectPackCommandHandlers as ProjectPackCommandHandlers
from thoth.application.commands.research_operations import (
    register_research_operations as register_research_operations,
)
from thoth.application.services import (
    AcquisitionCoordinator as AcquisitionCoordinator,
)
from thoth.application.services import (
    ConnectorService as ConnectorService,
)
from thoth.application.services import (
    ControlRecordService,
    InvestigationService,
)
from thoth.application.services import (
    CriticalCounterSearchCoordinator as CriticalCounterSearchCoordinator,
)
from thoth.application.services import (
    DecisionObjectService as DecisionObjectService,
)
from thoth.application.services import (
    OutcomeService as OutcomeService,
)
from thoth.application.services import (
    PolicyGate as PolicyGate,
)
from thoth.application.services import (
    RegisteredSandboxRouter as RegisteredSandboxRouter,
)
from thoth.application.services import (
    RevisionCommitService as RevisionCommitService,
)
from thoth.application.services import (
    SandboxService as SandboxService,
)
from thoth.apps.acquisition_composition import create_acquisition_components
from thoth.apps.behavior_composition import create_behavior_runtime
from thoth.apps.criteria_composition import create_criterion_service
from thoth.apps.criterion_object_routes import (
    register_criterion_and_object_methods as register_criterion_and_object_methods,
)
from thoth.apps.decision_chain_routes import (
    register_decision_chain_methods as register_decision_chain_methods,
)
from thoth.apps.decision_command_composition import create_decision_command_components
from thoth.apps.evidence_composition import create_evidence_components
from thoth.apps.export_composition import create_export_handlers
from thoth.apps.export_composition import create_lifecycle_handlers as create_lifecycle_handlers
from thoth.apps.history_command_composition import create_history_commands
from thoth.apps.improvement_composition import create_improvement_components
from thoth.apps.improvement_composition import (
    register_improvement_handlers as register_improvement_handlers,
)
from thoth.apps.judgment_review_composition import (
    install_judgment_review as install_judgment_review,
)
from thoth.apps.lifecycle_routes import (
    register_closure_and_export_methods as register_closure_and_export_methods,
)
from thoth.apps.management_routes import (
    register_investigation_methods as register_investigation_methods,
)
from thoth.apps.management_routes import register_project_methods as register_project_methods
from thoth.apps.management_routes import register_thread_methods as register_thread_methods
from thoth.apps.memory_composition import create_memory_components, create_memory_handlers
from thoth.apps.memory_composition import register_memory_handlers as register_memory_handlers
from thoth.apps.model_composition import create_models, wrap_research_models
from thoth.apps.outcome_receipt_routes import (
    register_outcome_and_receipt_methods as register_outcome_and_receipt_methods,
)
from thoth.apps.projectpack_execution import (
    ProjectPackExecutionFactory as ProjectPackExecutionFactory,
)
from thoth.apps.reference_composition import reference_thread_entry as reference_thread_entry
from thoth.apps.research_entry_composition import create_research_entry as create_research_entry
from thoth.apps.research_runtime import create_research_components
from thoth.apps.resource_scope_composition import (
    create_evidence_access,
    create_execution_access,
    create_memory_access,
    create_resources,
)
from thoth.apps.resource_scope_composition import (
    register_resource_handlers as register_resource_handlers,
)
from thoth.apps.resource_scope_composition import (
    scope_bus as scope_bus,
)
from thoth.apps.resource_scope_composition import (
    source_policy_defaults as source_policy_defaults,
)
from thoth.apps.revision_composition import create_revision_handlers as create_revision_handlers
from thoth.apps.revision_composition import register_revision_methods as register_revision_methods
from thoth.apps.runtime_entry import assemble_runtime_entry
from thoth.apps.runtime_registration import register_runtime_methods
from thoth.apps.runtime_types import AppRuntime, RuntimeOptions, RuntimeWithLedger
from thoth.apps.sandbox_composition import create_sandbox_components
from thoth.apps.source_command_composition import create_source_commands
from thoth.apps.source_composition import (
    create_ingestion_service,
    create_source_time_service,
)
from thoth.apps.source_composition import create_source_handlers as create_source_handlers
from thoth.apps.source_evidence_routes import (
    register_source_and_evidence_methods as register_source_and_evidence_methods,
)
from thoth.apps.storage_composition import create_shared_storage_services, open_stores
from thoth.apps.test_runtime import create_execution_components as create_execution_components
from thoth.apps.thread_analysis_composition import create_research_queries, create_thread_analysis
from thoth.domain.deployment_mode import DeploymentMode, parse_deployment_mode
from thoth.domain.environment import EnvironmentProfile
from thoth.domain.policy import preferred_hosts_from_payload as preferred_hosts_from_payload
from thoth.domain.public_web_access import (
    PROJECT_PUBLIC_WEB_CONNECTOR_ID as PROJECT_PUBLIC_WEB_CONNECTOR_ID,
)
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
from thoth.protocol.registry import MethodRegistry as MethodRegistry

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
    stores = open_stores(workspace, storage_factory, deployment_mode=mode)
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

        acquisition = create_acquisition_components(
            workspace=workspace,
            connector_registry=connector_registry,
            stores=stores,
            governance=governance,
            projects=projects,
            ingestion_service=ingestion_service,
            evidence_graph_service=evidence_graph_service,
            acquisition_fault_injector=acquisition_fault_injector,
            artifact_ledger=artifact_ledger,
            control_service=control_service,
            clock=clock,
            ids=ids,
            evidence_unit_of_work=evidence_unit_of_work,
            investigation_service=investigation_service,
            acquisition_trace_store=acquisition_trace_store,
            research_identity=research_identity,
            evidence_graph_store=evidence_graph_store,
            ledger=ledger,
        )
        connector_service = acquisition.connector_service
        effective_models = wrap_research_models(
            behavior_runtime, create_models(model_resolver, artifact_ledger.scopes, workspace)
        )
        sandbox = create_sandbox_components(
            sandbox_adapter=sandbox_adapter,
            artifact_ledger=artifact_ledger,
            object_store=object_store,
            control_service=control_service,
            ingestion_service=ingestion_service,
            projects=projects,
            governance=governance,
            ledger=ledger,
            clock=clock,
            ids=ids,
            stores=stores,
            research_components=research_components,
            action_store=action_store,
            action_service=action_service,
            execution_store=execution_store,
        )
        decision_commands = create_decision_command_components(
            action_service=action_service,
            action_store=action_store,
            artifact_ledger=artifact_ledger,
            baseline_service=baseline_service,
            clock=clock,
            criterion_service=criterion_service,
            criterion_store=criterion_store,
            decision_object_store=decision_object_store,
            dependency_graph=dependency_graph,
            effective_connectors=acquisition.effective_connectors,
            environment_profile=environment_profile,
            evidence_graph_service=evidence_graph_service,
            evidence_graph_store=evidence_graph_store,
            execution_service=sandbox.execution_service,
            execution_store=execution_store,
            governance=governance,
            hypothesis_service=hypothesis_service,
            hypothesis_store=hypothesis_store,
            ids=ids,
            investigation_service=investigation_service,
            ledger=ledger,
            operations=operations,
            outcome_store=outcome_store,
            projects=projects,
            resource_scope_policy=resource_scope_policy,
            sandbox_adapter=sandbox_adapter,
            sandbox_service=sandbox.sandbox_service,
            thread_store=thread_store,
            workspace_setup=acquisition.workspace_setup,
        )
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
            decision_objects=decision_commands.decision_object_service,
        )
        source_commands = create_source_commands(
            artifact_ledger=artifact_ledger,
            clock=clock,
            connector_service=connector_service,
            effective_models=effective_models,
            evidence_graph_service=evidence_graph_service,
            ids=ids,
            ingestion_service=ingestion_service,
            ledger=ledger,
            projectpack_root=projectpack_root,
            source_time_service=source_time_service,
            storage_factory=storage_factory,
            stores=stores,
            workspace=workspace,
        )
        thread_analysis_handlers = create_thread_analysis(
            stores,
            artifact_ledger,
            memory_store,
            criterion_store,
            criterion_service,
            effective_models,
            acquisition.acquisition_coordinator,
            acquisition.critical_counter_search,
            sandbox.r2_closed_loop,
            receipt_dag_service,
            full_memory_service,
            recursive_improvement,
            baseline_service,
            research_identity,
            clock,
            ids,
        )
        history_commands = create_history_commands(
            action_store=action_store,
            artifact_ledger=artifact_ledger,
            baseline_service=baseline_service,
            clock=clock,
            control_service=control_service,
            control_store=control_store,
            dependency_graph=dependency_graph,
            execution_store=execution_store,
            governance=governance,
            ids=ids,
            ledger=ledger,
            outcome_store=outcome_store,
            receipt_dag_service=receipt_dag_service,
            receipt_dag_store=receipt_dag_store,
            stores=stores,
        )
        queries = create_research_queries(
            acquisition_trace_store=acquisition_trace_store,
            artifact_ledger=artifact_ledger,
            clock=clock,
            investigation_service=investigation_service,
            investigation_store=investigation_store,
            ledger=ledger,
            research_identity=research_identity,
            thread_store=thread_store,
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
        registration = register_runtime_methods(
            artifact_ledger=artifact_ledger,
            baseline_service=baseline_service,
            behavior_runtime=behavior_runtime,
            clock=clock,
            control_service=control_service,
            criterion_service=criterion_service,
            criterion_store=criterion_store,
            decision_commands=decision_commands,
            effective_models=effective_models,
            evaluation_handlers=evaluation_handlers,
            export_handlers=export_handlers,
            field_measurement_handlers=field_measurement_handlers,
            history_commands=history_commands,
            hypothesis_store=hypothesis_store,
            ids=ids,
            improvement_handlers=improvement_handlers,
            ledger=ledger,
            memory_full_handlers=memory_full_handlers,
            projects=projects,
            research_queries=queries,
            source_commands=source_commands,
            stores=stores,
            thread_analysis_handlers=thread_analysis_handlers,
            thread_handlers=thread_handlers,
            thread_store=thread_store,
        )
        return assemble_runtime_entry(
            registration=registration,
            artifact_ledger=artifact_ledger,
            clock=clock,
            connector_service=connector_service,
            criterion_store=criterion_store,
            effective_models=effective_models,
            evidence_graph_store=evidence_graph_store,
            field_measurement_service=field_measurement_service,
            full_memory_service=full_memory_service,
            ids=ids,
            ledger=ledger,
            mode=mode,
            model_catalog=model_catalog,
            model_resolver=model_resolver,
            operations=operations,
            research_components=research_components,
            stores=stores,
            task_profiles=task_profiles,
            thread_handlers=thread_handlers,
            workspace=workspace,
        )
    except BaseException:
        stores.close()
        raise
