"""Compose core decision commands while preserving each profile seed transaction."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from thoth.application.commands import (
    ActionHandlers,
    CriterionFullHandlers,
    DecisionObjectHandlers,
    EvidenceCommandHandlers,
    ExecutionHandlers,
    HypothesisHandlers,
    OutcomeHandlers,
    ProjectCommandHandlers,
)
from thoth.application.services.action_service import ActionService
from thoth.application.services.baseline_service import BaselineService
from thoth.application.services.criterion_contract_service import CriterionContractService
from thoth.application.services.decision_object_service import DecisionObjectService
from thoth.application.services.evidence_graph_service import EvidenceGraphService
from thoth.application.services.execution_service import ExecutionService
from thoth.application.services.hypothesis_service import HypothesisService
from thoth.application.services.investigation_service import InvestigationService
from thoth.application.services.outcome_service import OutcomeService
from thoth.application.services.revision_service import RevisionCommitService
from thoth.application.services.sandbox_service import SandboxService
from thoth.apps.resource_scope_composition import source_policy_defaults
from thoth.domain.environment import EnvironmentProfile
from thoth.domain.public_web_access import PROJECT_PUBLIC_WEB_CONNECTOR_ID
from thoth.domain.resource_scope import ResourceScopePolicy
from thoth.domain.workspace_setup import WorkspaceSetupState
from thoth.ports.action import ActionStorePort
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.connectors import ConnectorRegistryPort
from thoth.ports.criterion_contract import CriterionContractStorePort
from thoth.ports.decision_object import DecisionObjectStorePort
from thoth.ports.dependency import DependencyGraphPort
from thoth.ports.evidence_graph import EvidenceGraphStorePort
from thoth.ports.execution import ExecutionStorePort
from thoth.ports.governance import GovernanceStorePort
from thoth.ports.hypothesis import HypothesisStorePort
from thoth.ports.ledger import ManagedLedgerPort
from thoth.ports.operation import OperationStorePort
from thoth.ports.outcome import OutcomeStorePort
from thoth.ports.project import ProjectStorePort
from thoth.ports.runtime import ClockPort, IdGeneratorPort
from thoth.ports.sandbox import SandboxPort
from thoth.ports.thread import ThreadStorePort


@dataclass(frozen=True)
class DecisionCommandComponents:
    decision_object_service: DecisionObjectService
    project_handlers: ProjectCommandHandlers
    evidence_handlers: EvidenceCommandHandlers
    criterion_handlers: CriterionFullHandlers
    object_handlers: DecisionObjectHandlers
    hypothesis_handlers: HypothesisHandlers
    action_handlers: ActionHandlers
    execution_handlers: ExecutionHandlers
    outcome_handlers: OutcomeHandlers


def create_decision_command_components(
    *,
    action_service: ActionService,
    action_store: ActionStorePort,
    artifact_ledger: ArtifactLedgerPort,
    baseline_service: BaselineService,
    clock: ClockPort,
    criterion_service: CriterionContractService,
    criterion_store: CriterionContractStorePort,
    decision_object_store: DecisionObjectStorePort,
    dependency_graph: DependencyGraphPort,
    effective_connectors: ConnectorRegistryPort,
    environment_profile: EnvironmentProfile | None,
    evidence_graph_service: EvidenceGraphService,
    evidence_graph_store: EvidenceGraphStorePort,
    execution_service: ExecutionService,
    execution_store: ExecutionStorePort,
    governance: GovernanceStorePort,
    hypothesis_service: HypothesisService,
    hypothesis_store: HypothesisStorePort,
    ids: IdGeneratorPort,
    investigation_service: InvestigationService,
    ledger: ManagedLedgerPort,
    operations: OperationStorePort,
    outcome_store: OutcomeStorePort,
    projects: ProjectStorePort,
    resource_scope_policy: ResourceScopePolicy | None,
    sandbox_adapter: SandboxPort | None,
    sandbox_service: SandboxService,
    thread_store: ThreadStorePort,
    workspace_setup: Callable[[], WorkspaceSetupState],
) -> DecisionCommandComponents:
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
    return DecisionCommandComponents(
        decision_object_service=decision_object_service,
        project_handlers=project_handlers,
        evidence_handlers=evidence_handlers,
        criterion_handlers=criterion_handlers,
        object_handlers=object_handlers,
        hypothesis_handlers=hypothesis_handlers,
        action_handlers=action_handlers,
        execution_handlers=execution_handlers,
        outcome_handlers=outcome_handlers,
    )
