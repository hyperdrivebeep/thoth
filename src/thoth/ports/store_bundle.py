"""A provider supplies one shared ledger/UoW and all canonical namespace stores."""

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from thoth.ports.acquisition import (
    AcquisitionTraceStorePort,
    AcquisitionUnitOfWorkPort,
    EvidenceUnitOfWorkPort,
)
from thoth.ports.action import ActionStorePort
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.auth import AuthSessionStorePort
from thoth.ports.baseline import BaselineStorePort
from thoth.ports.behavior_artifact import BehaviorArtifactStorePort
from thoth.ports.behavior_execution import BehaviorExecutionStorePort
from thoth.ports.control_record import ControlRecordStorePort
from thoth.ports.criterion_contract import CriterionContractStorePort
from thoth.ports.decision_object import DecisionObjectStorePort
from thoth.ports.dependency import DependencyGraphPort
from thoth.ports.evaluation_runner import EvaluationRunStorePort
from thoth.ports.event_store import EventStorePort
from thoth.ports.evidence_graph import EvidenceGraphStorePort
from thoth.ports.execution import ExecutionStorePort
from thoth.ports.field_measurement import FieldMeasurementStorePort
from thoth.ports.governance import GovernanceStorePort
from thoth.ports.hypothesis import HypothesisStorePort
from thoth.ports.investigation import InvestigationStorePort
from thoth.ports.ledger import LedgerPort, LedgerProjectionPort, ManagedLedgerPort
from thoth.ports.memory import FullMemoryStorePort, MemoryStorePort
from thoth.ports.object_store import ObjectStorePort
from thoth.ports.operation import OperationStorePort
from thoth.ports.outcome import OutcomeStorePort
from thoth.ports.project import ProjectStorePort
from thoth.ports.receipt_dag import ReceiptDagStorePort
from thoth.ports.research_history import ResearchHistoryReadPort
from thoth.ports.research_identity import ResearchIdentityStorePort
from thoth.ports.resource_scope import (
    ResourceAccessPort,
    ResourceRecordAccessPort,
    ResourceScopeAdmissionPort,
    ResourceScopeStorePort,
)
from thoth.ports.runtime import AtomicUnitOfWorkPort
from thoth.ports.test_validity import TestValidityStorePort
from thoth.ports.thread import ThreadStorePort
from thoth.ports.thread_runtime import ThreadRuntimeStorePort


class ResearchIdentityProjectionPort(ResearchIdentityStorePort, LedgerProjectionPort, Protocol):
    pass


class StoreBundlePort(Protocol):
    @property
    def research_history(self) -> ResearchHistoryReadPort: ...
    @property
    def ledger(self) -> ManagedLedgerPort: ...
    @property
    def objects(self) -> ObjectStorePort: ...
    @property
    def atomic_uow(self) -> AtomicUnitOfWorkPort: ...
    @property
    def immediate_uow(self) -> AtomicUnitOfWorkPort: ...
    @property
    def research_identity(self) -> ResearchIdentityProjectionPort: ...
    @property
    def operations(self) -> OperationStorePort: ...
    @property
    def projects(self) -> ProjectStorePort: ...
    @property
    def governance(self) -> GovernanceStorePort: ...
    @property
    def artifacts(self) -> ArtifactLedgerPort: ...
    @property
    def resource_scopes(self) -> ResourceScopeStorePort: ...
    @property
    def auth(self) -> AuthSessionStorePort: ...
    @property
    def evidence(self) -> EvidenceGraphStorePort: ...
    @property
    def controls(self) -> ControlRecordStorePort: ...
    @property
    def receipt_dag(self) -> ReceiptDagStorePort: ...
    @property
    def threads(self) -> ThreadStorePort: ...
    @property
    def thread_runtime(self) -> ThreadRuntimeStorePort: ...
    @property
    def acquisition_trace(self) -> AcquisitionTraceStorePort: ...
    @property
    def memory_candidates(self) -> MemoryStorePort: ...
    @property
    def baselines(self) -> BaselineStorePort: ...
    @property
    def behaviors(self) -> BehaviorArtifactStorePort: ...
    @property
    def dependencies(self) -> DependencyGraphPort: ...
    @property
    def events(self) -> EventStorePort: ...
    @property
    def executions(self) -> ExecutionStorePort: ...
    @property
    def field_measurement(self) -> FieldMeasurementStorePort: ...
    @property
    def evaluation_runs(self) -> EvaluationRunStorePort: ...
    @property
    def behavior_executions(self) -> BehaviorExecutionStorePort: ...
    @property
    def test_validity(self) -> TestValidityStorePort: ...
    def criteria(
        self, resource_access: ResourceAccessPort | None = None
    ) -> CriterionContractStorePort: ...
    def decision_objects(
        self, resource_access: ResourceRecordAccessPort | None = None
    ) -> DecisionObjectStorePort: ...
    def hypotheses(
        self, resource_access: ResourceAccessPort | None = None
    ) -> HypothesisStorePort: ...
    def actions(self, resource_access: ResourceAccessPort | None = None) -> ActionStorePort: ...
    def investigations(
        self, resource_access: ResourceRecordAccessPort | None = None
    ) -> InvestigationStorePort: ...
    def outcomes(self, resource_access: ResourceAccessPort | None = None) -> OutcomeStorePort: ...
    def full_memory(
        self, fault_injector: Callable[[str], None] | None = None
    ) -> FullMemoryStorePort: ...
    def acquisition_uow(
        self,
        resource_scopes: ResourceScopeAdmissionPort | None = None,
        fault_injector: Callable[[str], None] | None = None,
    ) -> AcquisitionUnitOfWorkPort: ...
    def evidence_uow(
        self,
        resource_scopes: ResourceScopeAdmissionPort | None = None,
        fault_injector: Callable[[str], None] | None = None,
    ) -> EvidenceUnitOfWorkPort: ...
    def close(self) -> None: ...
    def scoped_ledger(self, resource_access: ResourceAccessPort) -> LedgerPort: ...


class StoreFactoryPort(Protocol):
    @property
    def provider_id(self) -> str: ...
    @property
    def version(self) -> str: ...
    def open(self, workspace: Path) -> StoreBundlePort: ...


class StoreFactoryRegistryPort(Protocol):
    def resolve(self, provider_id: str, version: str) -> StoreFactoryPort: ...
