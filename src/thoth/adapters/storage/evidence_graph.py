from __future__ import annotations

from datetime import datetime
from typing import cast

import orjson
from sqlalchemy import Engine, Integer, insert, literal_column, select, update

from thoth.adapters.storage.schema import (
    evidence_audit,
    evidence_conflicts,
    evidence_links,
    evidence_observations,
    evidence_sources,
    span_corrections,
)
from thoth.adapters.storage.transaction import read_connection, write_connection
from thoth.domain.enums import AuthorityState, CutoffState, SupportState, VerificationState
from thoth.domain.evidence_graph import (
    EvidenceAuditRecord,
    EvidenceConflictRecord,
    EvidenceLinkRecord,
    EvidenceSourceRecord,
    ObservationRecord,
    SpanCorrectionRecord,
)
from thoth.ports.evidence_graph import EvidenceGraphStorePort


def dump(value: object) -> str:
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS).decode()


def strings(value: object) -> tuple[str, ...]:
    return tuple(str(item) for item in cast(list[object], orjson.loads(str(value))))


def mapping(value: object) -> dict[str, object]:
    loaded = cast(object, orjson.loads(str(value)))
    if not isinstance(loaded, dict):
        raise ValueError("stored evidence payload is not an object")
    return {str(key): child for key, child in cast(dict[object, object], loaded).items()}


class SqliteEvidenceGraphStore(EvidenceGraphStorePort):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add_source(self, value: EvidenceSourceRecord) -> None:
        with write_connection(self._engine) as connection:
            connection.execute(
                insert(evidence_sources).values(
                    source_id=value.source_id,
                    project_id=value.project_id,
                    artifact_id=value.artifact_id,
                    connector_ref=value.connector_ref,
                    uri=value.uri,
                    artifact_type=value.artifact_type,
                    version=value.version,
                    sha256=value.sha256,
                    authority_status=value.authority_status.value,
                    security_class=value.security_class,
                    official_copy=int(value.official_copy),
                    rights=value.rights,
                    retention=value.retention,
                    valid_time=None if value.valid_time is None else value.valid_time.isoformat(),
                    snapshot_time=(
                        None if value.snapshot_time is None else value.snapshot_time.isoformat()
                    ),
                    cutoff_eligibility=value.cutoff_eligibility.value,
                    lineage_root_id=value.lineage_root_id,
                    parent_source_ids_json=dump(value.parent_source_ids),
                    supersedes_source_id=value.supersedes_source_id,
                    source_digest=value.source_digest,
                    created_at=value.created_at.isoformat(),
                )
            )

    def read_source(self, source_id: str) -> EvidenceSourceRecord | None:
        with read_connection(self._engine) as connection:
            row = (
                connection.execute(
                    select(evidence_sources).where(evidence_sources.c.source_id == source_id)
                )
                .mappings()
                .first()
            )
        return None if row is None else self._source(row)

    def read_source_by_artifact(self, artifact_id: str) -> EvidenceSourceRecord | None:
        with read_connection(self._engine) as connection:
            row = (
                connection.execute(
                    select(evidence_sources)
                    .where(evidence_sources.c.artifact_id == artifact_id)
                    .order_by(
                        evidence_sources.c.created_at.desc(),
                        literal_column("rowid", Integer()).desc(),
                    )
                    .limit(1)
                )
                .mappings()
                .first()
            )
        return None if row is None else self._source(row)

    def list_sources(self, project_id: str) -> tuple[EvidenceSourceRecord, ...]:
        with read_connection(self._engine) as connection:
            rows = connection.execute(
                select(evidence_sources)
                .where(evidence_sources.c.project_id == project_id)
                .order_by(evidence_sources.c.created_at, literal_column("rowid", Integer()))
            ).mappings()
            return tuple(self._source(row) for row in rows)

    def add_observation(self, value: ObservationRecord) -> None:
        with write_connection(self._engine) as connection:
            connection.execute(
                insert(evidence_observations).values(
                    observation_id=value.observation_id,
                    project_id=value.project_id,
                    span_ids_json=dump(value.span_ids),
                    observed_statement=value.observed_statement,
                    observed_time=(
                        None if value.observed_time is None else value.observed_time.isoformat()
                    ),
                    valid_time=None if value.valid_time is None else value.valid_time.isoformat(),
                    observer_group=value.observer_group,
                    independence_basis=value.independence_basis,
                    provenance_class=value.provenance_class,
                    contamination_note=value.contamination_note,
                    supersedes_observation_id=value.supersedes_observation_id,
                    observation_digest=value.observation_digest,
                    created_at=value.created_at.isoformat(),
                )
            )

    def read_observation(self, observation_id: str) -> ObservationRecord | None:
        with read_connection(self._engine) as connection:
            row = (
                connection.execute(
                    select(evidence_observations).where(
                        evidence_observations.c.observation_id == observation_id
                    )
                )
                .mappings()
                .first()
            )
        return None if row is None else self._observation(row)

    def add_link(self, value: EvidenceLinkRecord) -> None:
        with write_connection(self._engine) as connection:
            connection.execute(
                insert(evidence_links).values(
                    evidence_id=value.evidence_id,
                    project_id=value.project_id,
                    thread_id=value.thread_id,
                    target_type=value.target_type,
                    target_id=value.target_id,
                    relation=value.relation,
                    source_ids_json=dump(value.source_ids),
                    span_ids_json=dump(value.span_ids),
                    observation_ids_json=dump(value.observation_ids),
                    conditions_json=dump(value.conditions),
                    applicability=value.applicability,
                    independence_group=value.independence_group,
                    support_status=value.support_status.value,
                    authority_status=value.authority_status.value,
                    verification_status=value.verification_status.value,
                    cutoff_eligibility=value.cutoff_eligibility.value,
                    content_trust=value.content_trust,
                    conflict_ids_json=dump(value.conflict_ids),
                    supersedes_evidence_id=value.supersedes_evidence_id,
                    revision=value.revision,
                    evidence_digest=value.evidence_digest,
                    created_at=value.created_at.isoformat(),
                )
            )

    def read_link(self, evidence_id: str) -> EvidenceLinkRecord | None:
        with read_connection(self._engine) as connection:
            row = (
                connection.execute(
                    select(evidence_links).where(evidence_links.c.evidence_id == evidence_id)
                )
                .mappings()
                .first()
            )
        return None if row is None else self._link(row)

    def list_links(
        self, project_id: str, target_id: str | None = None
    ) -> tuple[EvidenceLinkRecord, ...]:
        statement = select(evidence_links).where(evidence_links.c.project_id == project_id)
        if target_id is not None:
            statement = statement.where(evidence_links.c.target_id == target_id)
        with read_connection(self._engine) as connection:
            rows = connection.execute(
                statement.order_by(evidence_links.c.created_at, literal_column("rowid", Integer()))
            ).mappings()
            return tuple(self._link(row) for row in rows)

    def add_conflict(self, value: EvidenceConflictRecord) -> None:
        with write_connection(self._engine) as connection:
            connection.execute(insert(evidence_conflicts).values(**self._conflict_values(value)))

    def read_conflict(self, conflict_id: str) -> EvidenceConflictRecord | None:
        with read_connection(self._engine) as connection:
            row = (
                connection.execute(
                    select(evidence_conflicts).where(
                        evidence_conflicts.c.conflict_id == conflict_id
                    )
                )
                .mappings()
                .first()
            )
        return None if row is None else self._conflict(row)

    def list_conflicts(self, project_id: str) -> tuple[EvidenceConflictRecord, ...]:
        with read_connection(self._engine) as connection:
            rows = connection.execute(
                select(evidence_conflicts)
                .where(evidence_conflicts.c.project_id == project_id)
                .order_by(evidence_conflicts.c.created_at)
            ).mappings()
            return tuple(self._conflict(row) for row in rows)

    def update_conflict(self, value: EvidenceConflictRecord) -> None:
        with write_connection(self._engine) as connection:
            changed = connection.execute(
                update(evidence_conflicts)
                .where(evidence_conflicts.c.conflict_id == value.conflict_id)
                .values(**self._conflict_values(value))
            ).rowcount
            if changed != 1:
                raise KeyError(value.conflict_id)

    def append_audit(self, value: EvidenceAuditRecord) -> None:
        with write_connection(self._engine) as connection:
            connection.execute(
                insert(evidence_audit).values(
                    audit_id=value.audit_id,
                    project_id=value.project_id,
                    evidence_ref=value.evidence_ref,
                    event_type=value.event_type,
                    payload_json=dump(value.payload),
                    event_digest=value.event_digest,
                    created_at=value.created_at.isoformat(),
                )
            )

    def list_audit(
        self, project_id: str, evidence_ref: str | None, *, offset: int, limit: int
    ) -> tuple[EvidenceAuditRecord, ...]:
        statement = select(evidence_audit).where(evidence_audit.c.project_id == project_id)
        if evidence_ref is not None:
            statement = statement.where(evidence_audit.c.evidence_ref == evidence_ref)
        with read_connection(self._engine) as connection:
            rows = connection.execute(
                statement.order_by(evidence_audit.c.created_at).offset(offset).limit(limit)
            ).mappings()
            return tuple(
                EvidenceAuditRecord(
                    audit_id=str(row["audit_id"]),
                    project_id=str(row["project_id"]),
                    evidence_ref=str(row["evidence_ref"]),
                    event_type=str(row["event_type"]),
                    payload=mapping(row["payload_json"]),
                    event_digest=str(row["event_digest"]),
                    created_at=datetime.fromisoformat(str(row["created_at"])),
                )
                for row in rows
            )

    def add_span_correction(self, value: SpanCorrectionRecord) -> None:
        with write_connection(self._engine) as connection:
            connection.execute(
                insert(span_corrections).values(
                    correction_id=value.correction_id,
                    project_id=value.project_id,
                    span_id=value.span_id,
                    corrected_text=value.corrected_text,
                    reason=value.reason,
                    correction_digest=value.correction_digest,
                    created_at=value.created_at.isoformat(),
                )
            )

    def list_span_corrections(
        self, project_id: str, span_id: str
    ) -> tuple[SpanCorrectionRecord, ...]:
        with read_connection(self._engine) as connection:
            rows = connection.execute(
                select(span_corrections)
                .where(
                    span_corrections.c.project_id == project_id,
                    span_corrections.c.span_id == span_id,
                )
                .order_by(span_corrections.c.created_at)
            ).mappings()
            return tuple(
                SpanCorrectionRecord(
                    correction_id=str(row["correction_id"]),
                    project_id=str(row["project_id"]),
                    span_id=str(row["span_id"]),
                    corrected_text=str(row["corrected_text"]),
                    reason=str(row["reason"]),
                    correction_digest=str(row["correction_digest"]),
                    created_at=datetime.fromisoformat(str(row["created_at"])),
                )
                for row in rows
            )

    @staticmethod
    def _source(row: object) -> EvidenceSourceRecord:
        value = cast(dict[str, object], row)
        return EvidenceSourceRecord(
            source_id=str(value["source_id"]),
            project_id=str(value["project_id"]),
            artifact_id=str(value["artifact_id"]),
            connector_ref=str(value["connector_ref"]),
            uri=str(value["uri"]),
            artifact_type=str(value["artifact_type"]),
            version=None if value["version"] is None else str(value["version"]),
            sha256=str(value["sha256"]),
            authority_status=AuthorityState(str(value["authority_status"])),
            security_class=str(value["security_class"]),
            official_copy=bool(value["official_copy"]),
            rights=str(value["rights"]),
            retention=str(value["retention"]),
            valid_time=None
            if value["valid_time"] is None
            else datetime.fromisoformat(str(value["valid_time"])),
            snapshot_time=None
            if value["snapshot_time"] is None
            else datetime.fromisoformat(str(value["snapshot_time"])),
            cutoff_eligibility=CutoffState(str(value["cutoff_eligibility"])),
            lineage_root_id=str(value["lineage_root_id"]),
            parent_source_ids=strings(value["parent_source_ids_json"]),
            supersedes_source_id=None
            if value["supersedes_source_id"] is None
            else str(value["supersedes_source_id"]),
            source_digest=str(value["source_digest"]),
            created_at=datetime.fromisoformat(str(value["created_at"])),
        )

    @staticmethod
    def _observation(row: object) -> ObservationRecord:
        value = cast(dict[str, object], row)
        return ObservationRecord(
            observation_id=str(value["observation_id"]),
            project_id=str(value["project_id"]),
            span_ids=strings(value["span_ids_json"]),
            observed_statement=str(value["observed_statement"]),
            observed_time=None
            if value["observed_time"] is None
            else datetime.fromisoformat(str(value["observed_time"])),
            valid_time=None
            if value["valid_time"] is None
            else datetime.fromisoformat(str(value["valid_time"])),
            observer_group=str(value["observer_group"]),
            independence_basis=str(value["independence_basis"]),
            provenance_class=str(value["provenance_class"]),
            contamination_note=None
            if value["contamination_note"] is None
            else str(value["contamination_note"]),
            supersedes_observation_id=None
            if value["supersedes_observation_id"] is None
            else str(value["supersedes_observation_id"]),
            observation_digest=str(value["observation_digest"]),
            created_at=datetime.fromisoformat(str(value["created_at"])),
        )

    @staticmethod
    def _link(row: object) -> EvidenceLinkRecord:
        value = cast(dict[str, object], row)
        return EvidenceLinkRecord(
            evidence_id=str(value["evidence_id"]),
            project_id=str(value["project_id"]),
            thread_id=None if value["thread_id"] is None else str(value["thread_id"]),
            target_type=str(value["target_type"]),
            target_id=str(value["target_id"]),
            relation=str(value["relation"]),
            source_ids=strings(value["source_ids_json"]),
            span_ids=strings(value["span_ids_json"]),
            observation_ids=strings(value["observation_ids_json"]),
            conditions={str(k): str(v) for k, v in mapping(value["conditions_json"]).items()},
            applicability=str(value["applicability"]),
            independence_group=str(value["independence_group"]),
            support_status=SupportState(str(value["support_status"])),
            authority_status=AuthorityState(str(value["authority_status"])),
            verification_status=VerificationState(str(value["verification_status"])),
            cutoff_eligibility=CutoffState(str(value["cutoff_eligibility"])),
            content_trust=str(value["content_trust"]),
            conflict_ids=strings(value["conflict_ids_json"]),
            supersedes_evidence_id=None
            if value["supersedes_evidence_id"] is None
            else str(value["supersedes_evidence_id"]),
            revision=int(str(value["revision"])),
            evidence_digest=str(value["evidence_digest"]),
            created_at=datetime.fromisoformat(str(value["created_at"])),
        )

    @staticmethod
    def _conflict_values(value: EvidenceConflictRecord) -> dict[str, object]:
        return {
            "conflict_id": value.conflict_id,
            "project_id": value.project_id,
            "evidence_ids_json": dump(value.evidence_ids),
            "field": value.field,
            "status": value.status,
            "reason": value.reason,
            "resolution_evidence_ids_json": dump(value.resolution_evidence_ids),
            "conflict_digest": value.conflict_digest,
            "created_at": value.created_at.isoformat(),
            "updated_at": value.updated_at.isoformat(),
        }

    @staticmethod
    def _conflict(row: object) -> EvidenceConflictRecord:
        value = cast(dict[str, object], row)
        return EvidenceConflictRecord(
            conflict_id=str(value["conflict_id"]),
            project_id=str(value["project_id"]),
            evidence_ids=strings(value["evidence_ids_json"]),
            field=str(value["field"]),
            status=str(value["status"]),
            reason=str(value["reason"]),
            resolution_evidence_ids=strings(value["resolution_evidence_ids_json"]),
            conflict_digest=str(value["conflict_digest"]),
            created_at=datetime.fromisoformat(str(value["created_at"])),
            updated_at=datetime.fromisoformat(str(value["updated_at"])),
        )
