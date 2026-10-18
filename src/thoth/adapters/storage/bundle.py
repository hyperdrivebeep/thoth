"""SQLite implementation of the whole-store bundle; one engine owns every DB participant."""

from collections.abc import Callable
from pathlib import Path

from thoth.adapters.storage.acquisition import SqliteAcquisitionUnitOfWork
from thoth.adapters.storage.acquisition_trace import (
    SqliteAcquisitionTraceStore,
    SqliteEvidenceUnitOfWork,
)
from thoth.adapters.storage.action import SqliteActionStore
from thoth.adapters.storage.alembic_migrations import migrate_sqlite_database
from thoth.adapters.storage.artifacts import SqliteArtifactLedger
from thoth.adapters.storage.auth import SqliteAuthSessionStore
from thoth.adapters.storage.baseline import SqliteBaselineStore
from thoth.adapters.storage.behavior_artifact import SqliteBehaviorArtifactStore
from thoth.adapters.storage.behavior_execution import SqliteBehaviorExecutionStore
from thoth.adapters.storage.control_record import SqliteControlRecordStore
from thoth.adapters.storage.criterion_contract import SqliteCriterionContractStore
from thoth.adapters.storage.decision_object import SqliteDecisionObjectStore
from thoth.adapters.storage.dependency import SqliteDependencyGraph
from thoth.adapters.storage.evaluation_run import SqliteEvaluationRunStore
from thoth.adapters.storage.events import SqliteEventStore
from thoth.adapters.storage.evidence_graph import SqliteEvidenceGraphStore
from thoth.adapters.storage.execution import SqliteExecutionStore
from thoth.adapters.storage.field_measurement import SqliteFieldMeasurementStore
from thoth.adapters.storage.governance import SqliteGovernanceStore
from thoth.adapters.storage.hypothesis import SqliteHypothesisStore
from thoth.adapters.storage.investigation import SqliteInvestigationStore
from thoth.adapters.storage.memory import SqliteFullMemoryStore, SqliteMemoryStore
from thoth.adapters.storage.objects import ContentAddressedObjectStore
from thoth.adapters.storage.operations import SqliteOperationStore
from thoth.adapters.storage.outcome import SqliteOutcomeStore
from thoth.adapters.storage.projects import SqliteProjectStore
from thoth.adapters.storage.receipt_dag import SqliteReceiptDagStore
from thoth.adapters.storage.research_history import SqliteResearchHistoryReader
from thoth.adapters.storage.research_identity import SqliteResearchIdentityStore
from thoth.adapters.storage.resource_scope import SqliteResourceScopeStore
from thoth.adapters.storage.scoped_ledger import ScopedLedger
from thoth.adapters.storage.sqlite import SqliteLedger
from thoth.adapters.storage.test_validity import SqliteTestValidityStore
from thoth.adapters.storage.thread_runtime import SqliteThreadRuntimeStore
from thoth.adapters.storage.threads import SqliteThreadStore
from thoth.adapters.storage.transaction import SqliteAtomicUnitOfWork
from thoth.adapters.storage.workspace_owner import WorkspaceOwnership, acquire_workspace_owner
from thoth.domain.deployment_mode import DeploymentMode, parse_deployment_mode
from thoth.ports.resource_scope import (
    ResourceAccessPort,
    ResourceRecordAccessPort,
    ResourceScopeAdmissionPort,
)


class SqliteStoreBundle:
    def __init__(self, ledger: SqliteLedger, workspace: Path) -> None:
        self.ledger = ledger
        self._ownership: WorkspaceOwnership | None = None
        self.objects = ContentAddressedObjectStore(workspace)
        self.atomic_uow = SqliteAtomicUnitOfWork(ledger.engine)
        self.immediate_uow = SqliteAtomicUnitOfWork(ledger.engine, immediate=True)
        self.research_identity = SqliteResearchIdentityStore(ledger.engine, ledger)
        self.research_history = SqliteResearchHistoryReader(ledger.engine)
        self.operations = SqliteOperationStore(ledger.engine)
        self.projects = SqliteProjectStore(ledger.engine)
        self.governance = SqliteGovernanceStore(ledger.engine)
        self.artifacts = SqliteArtifactLedger(ledger.engine)
        self.resource_scopes = SqliteResourceScopeStore(ledger.engine)
        self.auth = SqliteAuthSessionStore(ledger.engine)
        self.evidence = SqliteEvidenceGraphStore(ledger.engine)
        self.controls = SqliteControlRecordStore(ledger.engine)
        self.receipt_dag = SqliteReceiptDagStore(ledger.engine)
        self.threads = SqliteThreadStore(ledger.engine)
        self.thread_runtime = SqliteThreadRuntimeStore(ledger.engine)
        self.acquisition_trace = SqliteAcquisitionTraceStore(ledger.engine)
        self.memory_candidates = SqliteMemoryStore(ledger.engine)
        self.baselines = SqliteBaselineStore(ledger.engine)
        self.behaviors = SqliteBehaviorArtifactStore(ledger.engine)
        self.dependencies = SqliteDependencyGraph(ledger.engine)
        self.events = SqliteEventStore(ledger.engine)
        self.executions = SqliteExecutionStore(ledger.engine)
        self.field_measurement = SqliteFieldMeasurementStore(ledger.engine)
        self.evaluation_runs = SqliteEvaluationRunStore(ledger.engine)
        self.behavior_executions = SqliteBehaviorExecutionStore(ledger.engine)
        self.test_validity = SqliteTestValidityStore(ledger.engine)

    def criteria(
        self, resource_access: ResourceAccessPort | None = None
    ) -> SqliteCriterionContractStore:
        return SqliteCriterionContractStore(self.ledger.engine, self.ledger, resource_access)

    def decision_objects(
        self, resource_access: ResourceRecordAccessPort | None = None
    ) -> SqliteDecisionObjectStore:
        return SqliteDecisionObjectStore(self.ledger.engine, resource_access)

    def hypotheses(
        self, resource_access: ResourceAccessPort | None = None
    ) -> SqliteHypothesisStore:
        return SqliteHypothesisStore(self.ledger.engine, self.ledger, resource_access)

    def actions(self, resource_access: ResourceAccessPort | None = None) -> SqliteActionStore:
        return SqliteActionStore(self.ledger.engine, self.ledger, resource_access)

    def investigations(
        self, resource_access: ResourceRecordAccessPort | None = None
    ) -> SqliteInvestigationStore:
        return SqliteInvestigationStore(self.ledger.engine, resource_access)

    def outcomes(self, resource_access: ResourceAccessPort | None = None) -> SqliteOutcomeStore:
        return SqliteOutcomeStore(self.ledger.engine, resource_access)

    def full_memory(
        self, fault_injector: Callable[[str], None] | None = None
    ) -> SqliteFullMemoryStore:
        return SqliteFullMemoryStore(self.ledger.engine, fault_injector=fault_injector)

    def acquisition_uow(
        self,
        resource_scopes: ResourceScopeAdmissionPort | None = None,
        fault_injector: Callable[[str], None] | None = None,
    ) -> SqliteAcquisitionUnitOfWork:
        return SqliteAcquisitionUnitOfWork(
            self.ledger.engine, resource_scopes=resource_scopes, fault_injector=fault_injector
        )

    def evidence_uow(
        self,
        resource_scopes: ResourceScopeAdmissionPort | None = None,
        fault_injector: Callable[[str], None] | None = None,
    ) -> SqliteEvidenceUnitOfWork:
        return SqliteEvidenceUnitOfWork(
            self.ledger.engine, resource_scopes=resource_scopes, fault_injector=fault_injector
        )

    def hold_workspace(self, ownership: WorkspaceOwnership | None) -> None:
        """Release the workspace together with this bundle."""
        self._ownership = ownership

    def close(self) -> None:
        try:
            self.ledger.close()
        finally:
            ownership, self._ownership = self._ownership, None
            if ownership is not None:
                ownership.release()

    def scoped_ledger(self, resource_access: ResourceAccessPort) -> ScopedLedger:
        return ScopedLedger(self.ledger, resource_access)


class SqliteStoreFactory:
    provider_id = "sqlite"
    version = "1.0.0"

    def open(self, workspace: Path) -> SqliteStoreBundle:
        # One THOTH process per local workspace; the hosted review mode keeps one runtime per
        # session workspace by its own deployment rule and is not locked here.
        ownership = (
            None
            if parse_deployment_mode() is DeploymentMode.HOSTED_REVIEW
            else acquire_workspace_owner(workspace)
        )
        try:
            database = workspace / "db" / "thoth.sqlite3"
            migrate_sqlite_database(database)
            ledger = SqliteLedger(database)
            try:
                ledger.initialize()
                bundle = SqliteStoreBundle(ledger, workspace)
                bundle.hold_workspace(ownership)
                return bundle
            except BaseException:
                ledger.close()
                raise
        except BaseException:
            if ownership is not None:
                ownership.release()
            raise
