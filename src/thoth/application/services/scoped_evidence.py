"""Evidence queries and mutations retain owner scope and every source parent."""

from thoth.application.services.resource_scope_service import ResourceScopeService
from thoth.domain.evidence_graph import (
    EvidenceAuditRecord,
    EvidenceConflictRecord,
    EvidenceLinkRecord,
    EvidenceSourceRecord,
    ObservationRecord,
    SpanCorrectionRecord,
)
from thoth.ports.evidence_graph import EvidenceGraphStorePort
from thoth.ports.ledger import LedgerPort


class ScopedEvidenceGraphStore(EvidenceGraphStorePort):
    def __init__(
        self, raw: EvidenceGraphStorePort, scopes: ResourceScopeService, ledger: LedgerPort
    ) -> None:
        self._raw = raw
        self._scopes = scopes
        self._ledger = ledger

    def add_source(self, value: EvidenceSourceRecord) -> None:
        with self._ledger.transaction():
            self._scopes.require_write(value.project_id, value.artifact_id)
            self._raw.add_source(value)

    def read_source(self, source_id: str) -> EvidenceSourceRecord | None:
        value = self._raw.read_source(source_id)
        if value is not None:
            self._scopes.require_read(value.project_id, value.artifact_id)
        return value

    def read_source_by_artifact(self, artifact_id: str) -> EvidenceSourceRecord | None:
        value = self._raw.read_source_by_artifact(artifact_id)
        if value is not None:
            self._scopes.require_read(value.project_id, value.artifact_id)
        return value

    def list_sources(self, project_id: str) -> tuple[EvidenceSourceRecord, ...]:
        return tuple(
            value
            for value in self._raw.list_sources(project_id)
            if self._scopes.may_read(project_id, value.artifact_id)
        )

    def add_observation(self, value: ObservationRecord) -> None:
        with self._ledger.transaction():
            is_new = self._raw.read_observation(value.observation_id) is None
            if not is_new:
                self._scopes.require_write(value.project_id, value.observation_id)
            self._raw.add_observation(value)
            self._scopes.ensure_derived(
                value.project_id, value.observation_id, value.span_ids, is_new=is_new
            )

    def read_observation(self, observation_id: str) -> ObservationRecord | None:
        value = self._raw.read_observation(observation_id)
        if value is not None:
            self._scopes.require_read(value.project_id, observation_id)
        return value

    def add_link(self, value: EvidenceLinkRecord) -> None:
        with self._ledger.transaction():
            is_new = self._raw.read_link(value.evidence_id) is None
            if not is_new:
                self._scopes.require_write(value.project_id, value.evidence_id)
            self._raw.add_link(value)
            self._scopes.ensure_derived(
                value.project_id,
                value.evidence_id,
                (*value.source_ids, *value.span_ids, *value.observation_ids),
                is_new=is_new,
            )

    def read_link(self, evidence_id: str) -> EvidenceLinkRecord | None:
        value = self._raw.read_link(evidence_id)
        if value is not None:
            self._scopes.require_read(value.project_id, evidence_id)
        return value

    def list_links(
        self, project_id: str, target_id: str | None = None
    ) -> tuple[EvidenceLinkRecord, ...]:
        return tuple(
            value
            for value in self._raw.list_links(project_id, target_id)
            if self._scopes.may_read(project_id, value.evidence_id)
        )

    def add_conflict(self, value: EvidenceConflictRecord) -> None:
        with self._ledger.transaction():
            self._raw.add_conflict(value)
            self._scopes.ensure_derived(
                value.project_id,
                value.conflict_id,
                (*value.evidence_ids, *value.resolution_evidence_ids),
                is_new=True,
            )

    def read_conflict(self, conflict_id: str) -> EvidenceConflictRecord | None:
        value = self._raw.read_conflict(conflict_id)
        if value is not None:
            self._scopes.require_read(value.project_id, conflict_id)
        return value

    def list_conflicts(self, project_id: str) -> tuple[EvidenceConflictRecord, ...]:
        return tuple(
            value
            for value in self._raw.list_conflicts(project_id)
            if self._scopes.may_read(project_id, value.conflict_id)
        )

    def update_conflict(self, value: EvidenceConflictRecord) -> None:
        with self._ledger.transaction():
            self._scopes.require_write(value.project_id, value.conflict_id)
            self._raw.update_conflict(value)
            self._scopes.ensure_derived(
                value.project_id,
                value.conflict_id,
                (*value.evidence_ids, *value.resolution_evidence_ids),
                is_new=False,
            )

    def append_audit(self, value: EvidenceAuditRecord) -> None:
        self._raw.append_audit(value)

    def list_audit(
        self, project_id: str, evidence_ref: str | None, *, offset: int, limit: int
    ) -> tuple[EvidenceAuditRecord, ...]:
        if evidence_ref is not None:
            self._scopes.require_read(project_id, evidence_ref)
        return tuple(
            value
            for value in self._raw.list_audit(project_id, evidence_ref, offset=offset, limit=limit)
            if self._scopes.may_read(project_id, value.evidence_ref)
        )

    def add_span_correction(self, value: SpanCorrectionRecord) -> None:
        with self._ledger.transaction():
            self._scopes.require_write(value.project_id, value.span_id)
            self._raw.add_span_correction(value)
            self._scopes.ensure_derived(
                value.project_id, value.correction_id, (value.span_id,), is_new=True
            )

    def list_span_corrections(
        self, project_id: str, span_id: str
    ) -> tuple[SpanCorrectionRecord, ...]:
        self._scopes.require_read(project_id, span_id)
        return tuple(
            value
            for value in self._raw.list_span_corrections(project_id, span_id)
            if self._scopes.may_read(project_id, value.correction_id)
        )
