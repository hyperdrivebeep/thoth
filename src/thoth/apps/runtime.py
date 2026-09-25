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
from thoth.apps.decision_chain_routes import register_decision_chain_methods
from thoth.apps.evidence_composition import create_evidence_components
from thoth.apps.export_composition import create_export_handlers, create_lifecycle_handlers
from thoth.apps.improvement_composition import (
    create_improvement_components,
    register_improvement_handlers,
)
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
        )
        execution_handlers = ExecutionHandlers(
            store=execution_store,
            actions=action_store,
            service=execution_service,
            sandbox=sandbox_service,
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
            control_store,
            memory_store,
            full_memory_store,
            ledger,
            clock,
            ids,
            artifact_ledger.scopes,
        )
        memory_full_handlers.seed_policies()
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
        registry.register("closure/prepare", lifecycle_handlers.prepare_closure)
        registry.register("closure/read", lifecycle_handlers.read_closure)
        registry.register("closure/finalize", lifecycle_handlers.finalize_closure)
        registry.register("closure/list", closure_handlers.list)
        registry.register("closure/readiness/read", closure_handlers.readiness_read)
        registry.register("closure/package/read", closure_handlers.package_read)
        registry.register("closure/openItem/list", closure_handlers.open_item_list)
        registry.register("closure/reopen/read", closure_handlers.reopen_read)
        registry.register("closure/retention/read", closure_handlers.retention_read)
        registry.register("closure/audit/read", closure_handlers.audit_read)
        registry.register("closure/readiness/assess", closure_handlers.readiness_assess)
        registry.register("closure/decide", closure_handlers.decide)
        registry.register("closure/followup/create", closure_handlers.followup_create)
        registry.register("closure/reopen", closure_handlers.reopen)
        registry.register("closure/retention/plan", closure_handlers.retention_plan)
        registry.register("closure/purge/prepare", closure_handlers.purge_prepare)
        registry.register("export/prepare", lifecycle_handlers.prepare_export)
        registry.register("export/list", export_handlers.list)
        registry.register("export/read", export_handlers.read)
        registry.register("export/plan/read", export_handlers.plan_read)
        registry.register("export/snapshot/read", export_handlers.snapshot_read)
        registry.register("export/manifest/read", export_handlers.manifest_read)
        registry.register("export/artifact/list", export_handlers.artifact_list)
        registry.register("export/verification/read", export_handlers.verification_read)
        registry.register("export/release/read", export_handlers.release_read)
        registry.register("export/correction/read", export_handlers.correction_read)
        registry.register("export/audit/read", export_handlers.audit_read)
        registry.register("export/plan/create", export_handlers.plan_create)
        registry.register("export/snapshot/create", export_handlers.snapshot_create)
        registry.register("export/generate", export_handlers.generate)
        registry.register("export/verify", export_handlers.verify)
        registry.register("export/release/prepare", export_handlers.release_prepare)
        registry.register("export/correction/create", export_handlers.correction_create)
        registry.register("project/source/connect", source_handlers.connect)
        registry.register("project/source/disconnect", source_handlers.disconnect)
        registry.register("project/source/list", source_handlers.list_sources)
        registry.register("project/source/time/confirm", source_handlers.confirm_time)
        registry.register("project/source/time/correct", source_handlers.correct_time)
        registry.register("evidence/list", evidence_handlers.list)
        registry.register("evidence/read", evidence_handlers.read)
        registry.register("evidence/packet/read", evidence_handlers.packet_read)
        registry.register("evidence/conflict/list", evidence_handlers.conflict_list)
        registry.register("evidence/conflict/read", evidence_handlers.conflict_read)
        registry.register("evidence/audit/read", evidence_handlers.audit_read)
        registry.register("evidence/source/add", evidence_handlers.source_add)
        registry.register("evidence/source/refresh", evidence_handlers.source_refresh)
        registry.register(
            "evidence/source/metadata/correct", evidence_handlers.source_metadata_correct
        )
        registry.register("evidence/span/correct", evidence_handlers.span_correct)
        registry.register("evidence/link/propose", evidence_handlers.link_propose)
        registry.register("evidence/link/correct", evidence_handlers.link_correct)
        registry.register("evidence/challenge", evidence_handlers.challenge)
        registry.register("evidence/revalidate", evidence_handlers.revalidate)
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
        registry.register("criteria/list", criterion_handlers.list)
        registry.register("criteria/read", criterion_handlers.read)
        registry.register("criteria/profile/list", criterion_handlers.profile_list)
        registry.register("criteria/profile/read", criterion_handlers.profile_read)
        registry.register("criteria/reference/list", criterion_handlers.reference_list)
        registry.register("criteria/reference/read", criterion_handlers.reference_read)
        registry.register("criteria/conflict/list", criterion_handlers.conflict_list)
        registry.register("criteria/conflict/read", criterion_handlers.conflict_read)
        registry.register("criteria/audit/read", criterion_handlers.audit_read)
        registry.register("criteria/compile", criterion_handlers.compile)
        registry.register("criteria/field/correct", criterion_handlers.field_correct)
        registry.register("criteria/profile/apply", criterion_handlers.profile_apply)
        registry.register("criteria/revalidate", criterion_handlers.revalidate)
        registry.register("criteria/recalculate", criterion_handlers.recalculate)
        registry.register("criteria/reference/generate", criterion_handlers.reference_generate)
        registry.register("criteria/change/propose", criterion_handlers.change_propose)
        registry.register("object/list", object_handlers.list)
        registry.register("object/read", object_handlers.read)
        registry.register("object/candidate/list", object_handlers.candidate_list)
        registry.register("object/candidate/read", object_handlers.candidate_read)
        registry.register("object/profile/list", object_handlers.profile_list)
        registry.register("object/profile/read", object_handlers.profile_read)
        registry.register("object/relation/list", object_handlers.relation_list)
        registry.register("object/impact/read", object_handlers.impact_read)
        registry.register("object/attention/list", object_handlers.attention_list)
        registry.register("object/audit/read", object_handlers.audit_read)
        registry.register("object/materialize", object_handlers.materialize)
        registry.register("object/frame/revise", object_handlers.frame_revise)
        registry.register("object/profile/apply", object_handlers.profile_apply)
        registry.register("object/facet/update", object_handlers.facet_update)
        registry.register("object/relation/add", object_handlers.relation_add)
        registry.register("object/relation/remove", object_handlers.relation_remove)
        registry.register("object/revalidate", object_handlers.revalidate)
        registry.register("object/work/replan", object_handlers.work_replan)
        registry.register("object/split/propose", object_handlers.split_propose)
        registry.register("object/merge/propose", object_handlers.merge_propose)
        registry.register("object/attention/acknowledge", object_handlers.attention_acknowledge)
        registry.register("object/followup/create", object_handlers.followup_create)
        register_memory_handlers(registry, memory_full_handlers)
        register_improvement_handlers(registry, improvement_handlers, evaluation_handlers)
        registry.register("outcome/list", outcome_handlers.list)
        registry.register("outcome/read", outcome_handlers.read)
        registry.register("outcome/series/list", outcome_handlers.series_list)
        registry.register("outcome/series/read", outcome_handlers.series_read)
        registry.register("outcome/profile/list", outcome_handlers.profile_list)
        registry.register("outcome/profile/read", outcome_handlers.profile_read)
        registry.register("outcome/attribution/read", outcome_handlers.attribution_read)
        registry.register("outcome/changeSet/read", outcome_handlers.change_set_read)
        registry.register("outcome/impact/read", outcome_handlers.impact_read)
        registry.register("outcome/audit/read", outcome_handlers.audit_read)
        registry.register("outcome/series/create", outcome_handlers.series_create)
        registry.register("outcome/observation/link", outcome_handlers.observation_link)
        registry.register("outcome/assess", outcome_handlers.assess)
        registry.register("outcome/reassess", outcome_handlers.reassess)
        registry.register("outcome/attribution/assess", outcome_handlers.attribution_assess)
        registry.register("outcome/changeSet/propose", outcome_handlers.change_set_propose)
        registry.register("outcome/followup/generate", outcome_handlers.followup_generate)
        registry.register("outcome/impact/propose", outcome_handlers.impact_propose)
        registry.register("receipt/list", receipt_handlers.list)
        registry.register("receipt/read", receipt_handlers.read)
        registry.register("receipt/stream/read", receipt_handlers.stream_read)
        registry.register("receipt/lineage/read", receipt_handlers.lineage_read)
        registry.register("receipt/bundle/read", receipt_handlers.bundle_read)
        registry.register("receipt/verification/read", receipt_handlers.verification_read)
        registry.register("receipt/audit/read", receipt_handlers.audit_read)
        registry.register("receipt/seal", receipt_handlers.seal)
        registry.register("receipt/verify", receipt_handlers.verify)
        registry.register("receipt/bundle/create", receipt_handlers.bundle_create)
        registry.register("receipt/bundle/verify", receipt_handlers.bundle_verify)
        registry.register("receipt/correction/create", receipt_handlers.correction_create)
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
        from thoth.application.commands.research_queue_cancel import QueueAwareOperationCancel

        registry.decorate(
            "operation/cancel",
            lambda original: QueueAwareOperationCancel(
                original, research_component.entry.records, research_component.entry.queue
            ).cancel,
        )

        register_research_operations(registry, research_component.entry)
        return RuntimeWithLedger(
            bus=bus, ledger=ledger, close_storage=research_component.close_storage
        )
    except BaseException:
        stores.close()
        raise
