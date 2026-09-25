"""Read execution facts through their frozen plan and observed source dependencies."""

from thoth.domain.execution_full import (
    ExecutionAuditRecord,
    ExecutionEffectRecord,
    PlanExecutionRecord,
    ReconciliationRecord,
    StepExecutionAttemptRecord,
)
from thoth.domain.resource_scope import ResourceScopeError
from thoth.ports.execution import ExecutionStorePort
from thoth.ports.resource_scope import ResourceAccessPort


class ScopedExecutionStore:
    def __init__(self, raw: ExecutionStorePort, access: ResourceAccessPort) -> None:
        self._raw = raw
        self._access = access

    def _reconciliation_sources(self, record: ReconciliationRecord) -> None:
        attempt = self._raw.read_attempt(record.project_id, record.attempt_id)
        if attempt is None or attempt.plan_execution_id != record.plan_execution_id:
            raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")
        self._access.require_revision(record.project_id, attempt.plan_revision_digest)
        self._access.require_reads(record.project_id, record.target_state_evidence_refs)

    def _execution(self, record: PlanExecutionRecord) -> None:
        self._access.require_revision(record.project_id, record.plan_revision_digest)
        self._access.require_reads(record.project_id, record.observation_refs)
        for identifier in record.reconciliation_refs:
            reconciliation = self._raw.read_reconciliation(record.project_id, identifier)
            if (
                reconciliation is None
                or reconciliation.plan_execution_id != record.plan_execution_id
            ):
                raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")
            self._reconciliation_sources(reconciliation)

    def _attempt(self, record: StepExecutionAttemptRecord) -> None:
        self._access.require_revision(record.project_id, record.plan_revision_digest)
        self._access.require_reads(record.project_id, record.observation_refs)
        execution = self._raw.read_execution(record.project_id, record.plan_execution_id)
        if execution is None:
            raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")
        self._execution(execution)

    def _visible(self, record: PlanExecutionRecord | StepExecutionAttemptRecord) -> bool:
        try:
            if isinstance(record, PlanExecutionRecord):
                self._execution(record)
            else:
                self._attempt(record)
        except ResourceScopeError as exc:
            if exc.code in {
                "RESOURCE_ACCESS_DENIED",
                "RESOURCE_SCOPE_UNKNOWN",
                "RESOURCE_REFERENCE_UNRESOLVED",
                "RESOURCE_LINEAGE_UNKNOWN",
            }:
                return False
            raise
        return True

    def add_execution(self, value: PlanExecutionRecord) -> None:
        self._raw.add_execution(value)

    def list_executions(self, project_id: str) -> tuple[PlanExecutionRecord, ...]:
        return tuple(
            value for value in self._raw.list_executions(project_id) if self._visible(value)
        )

    def read_execution(self, project_id: str, plan_execution_id: str) -> PlanExecutionRecord | None:
        value = self._raw.read_execution(project_id, plan_execution_id)
        if value is not None:
            self._execution(value)
        return value

    def add_attempt(self, value: StepExecutionAttemptRecord) -> None:
        self._raw.add_attempt(value)

    def list_attempts(
        self, project_id: str, plan_execution_id: str
    ) -> tuple[StepExecutionAttemptRecord, ...]:
        return tuple(
            value
            for value in self._raw.list_attempts(project_id, plan_execution_id)
            if self._visible(value)
        )

    def read_attempt(self, project_id: str, attempt_id: str) -> StepExecutionAttemptRecord | None:
        value = self._raw.read_attempt(project_id, attempt_id)
        if value is not None:
            self._attempt(value)
        return value

    def add_effect(self, value: ExecutionEffectRecord) -> None:
        self._raw.add_effect(value)

    def list_effects(self, project_id: str, attempt_id: str) -> tuple[ExecutionEffectRecord, ...]:
        if self.read_attempt(project_id, attempt_id) is None:
            raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")
        return self._raw.list_effects(project_id, attempt_id)

    def add_reconciliation(self, value: ReconciliationRecord) -> None:
        self._raw.add_reconciliation(value)

    def read_reconciliation(
        self, project_id: str, reconciliation_id: str
    ) -> ReconciliationRecord | None:
        value = self._raw.read_reconciliation(project_id, reconciliation_id)
        if value is not None:
            self._reconciliation_sources(value)
        return value

    def append_audit(self, value: ExecutionAuditRecord) -> None:
        self._raw.append_audit(value)

    def list_audit(
        self, project_id: str, plan_execution_id: str
    ) -> tuple[ExecutionAuditRecord, ...]:
        if self.read_execution(project_id, plan_execution_id) is None:
            raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")
        return self._raw.list_audit(project_id, plan_execution_id)
