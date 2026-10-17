"""Own the ordered RPC registration table for already constructed runtime components."""

from __future__ import annotations

from dataclasses import dataclass

from thoth.application.commands import FieldMeasurementHandlers, ThreadCommandHandlers
from thoth.application.commands.evaluation_runs import EvaluationRunHandlers
from thoth.application.commands.export_full import ExportHandlers
from thoth.application.commands.improvement_full import ImprovementHandlers
from thoth.application.commands.thread_analysis import ThreadAnalysisCommandHandlers
from thoth.application.services.baseline_service import BaselineService
from thoth.application.services.control_record_service import ControlRecordService
from thoth.application.services.criterion_contract_service import CriterionContractService
from thoth.application.services.scoped_artifacts import ScopedArtifactLedger
from thoth.apps.behavior_composition import BehaviorRuntime
from thoth.apps.criterion_object_routes import register_criterion_and_object_methods
from thoth.apps.decision_chain_routes import register_decision_chain_methods
from thoth.apps.decision_command_composition import DecisionCommandComponents
from thoth.apps.history_command_composition import HistoryCommands
from thoth.apps.improvement_composition import register_improvement_handlers
from thoth.apps.judgment_review_composition import JudgmentReviewWiring, install_judgment_review
from thoth.apps.lifecycle_routes import register_closure_and_export_methods
from thoth.apps.management_routes import (
    register_investigation_methods,
    register_project_methods,
    register_thread_methods,
)
from thoth.apps.memory_composition import MemoryHandlers, register_memory_handlers
from thoth.apps.outcome_receipt_routes import register_outcome_and_receipt_methods
from thoth.apps.reference_composition import reference_thread_entry
from thoth.apps.resource_scope_composition import register_resource_handlers
from thoth.apps.revision_composition import register_revision_methods
from thoth.apps.source_command_composition import SourceCommands
from thoth.apps.source_evidence_routes import register_source_and_evidence_methods
from thoth.apps.thread_analysis_composition import ResearchQueries
from thoth.ports.criterion_contract import CriterionContractStorePort
from thoth.ports.hypothesis import HypothesisStorePort
from thoth.ports.ledger import ManagedLedgerPort
from thoth.ports.model import ModelResolverPort
from thoth.ports.project import ProjectStorePort
from thoth.ports.runtime import ClockPort, IdGeneratorPort
from thoth.ports.store_bundle import StoreBundlePort
from thoth.ports.thread import ThreadStorePort
from thoth.protocol.registry import MethodRegistry


@dataclass(frozen=True)
class RuntimeRegistration:
    registry: MethodRegistry
    judgment_review: JudgmentReviewWiring


def register_runtime_methods(
    *,
    artifact_ledger: ScopedArtifactLedger,
    baseline_service: BaselineService,
    behavior_runtime: BehaviorRuntime,
    clock: ClockPort,
    control_service: ControlRecordService,
    criterion_service: CriterionContractService,
    criterion_store: CriterionContractStorePort,
    decision_commands: DecisionCommandComponents,
    effective_models: ModelResolverPort,
    evaluation_handlers: EvaluationRunHandlers,
    export_handlers: ExportHandlers,
    field_measurement_handlers: FieldMeasurementHandlers,
    history_commands: HistoryCommands,
    hypothesis_store: HypothesisStorePort,
    ids: IdGeneratorPort,
    improvement_handlers: ImprovementHandlers,
    ledger: ManagedLedgerPort,
    memory_full_handlers: MemoryHandlers,
    projects: ProjectStorePort,
    research_queries: ResearchQueries,
    source_commands: SourceCommands,
    stores: StoreBundlePort,
    thread_analysis_handlers: ThreadAnalysisCommandHandlers,
    thread_handlers: ThreadCommandHandlers,
    thread_store: ThreadStorePort,
) -> RuntimeRegistration:
    registry = MethodRegistry()
    register_resource_handlers(registry, artifact_ledger.scopes)
    registry.register("field/protocol/seal", field_measurement_handlers.protocol_seal)
    registry.register("field/session/start", field_measurement_handlers.session_start)
    registry.register("field/event/record", field_measurement_handlers.event_record)
    registry.register("field/session/end", field_measurement_handlers.session_end)
    registry.register("field/score/record", field_measurement_handlers.score_record)
    registry.register("field/export/build", field_measurement_handlers.export_build)
    registry.register("projectpack/list", source_commands.projectpack_handlers.list)
    registry.register("projectpack/run", source_commands.projectpack_handlers.run)
    register_project_methods(registry, decision_commands.project_handlers, stores)
    register_closure_and_export_methods(
        registry,
        history_commands.lifecycle_handlers,
        history_commands.closure_handlers,
        export_handlers,
    )
    register_source_and_evidence_methods(
        registry, source_commands.source_handlers, decision_commands.evidence_handlers
    )
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
        history_commands.revision_handlers,
        history_commands.revision_full_handlers,
        stores,
        artifact_ledger.scopes,
        baseline_service,
        clock,
        ids,
    )
    register_decision_chain_methods(
        registry,
        decision_commands.hypothesis_handlers,
        decision_commands.action_handlers,
        decision_commands.execution_handlers,
        research_queries.projection_handlers,
    )
    judgment_review = install_judgment_review(
        registry, stores, control_service, hypothesis_store, clock, ids
    )
    register_criterion_and_object_methods(
        registry, decision_commands.criterion_handlers, decision_commands.object_handlers
    )
    register_memory_handlers(registry, memory_full_handlers)
    register_improvement_handlers(registry, improvement_handlers, evaluation_handlers)
    register_outcome_and_receipt_methods(
        registry, decision_commands.outcome_handlers, history_commands.receipt_handlers
    )
    register_investigation_methods(registry, research_queries.investigation_handlers)
    return RuntimeRegistration(registry=registry, judgment_review=judgment_review)
