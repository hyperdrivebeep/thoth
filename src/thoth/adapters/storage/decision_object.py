from __future__ import annotations

from datetime import datetime
from typing import cast

import orjson
from sqlalchemy import Engine, insert, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from thoth.adapters.storage.schema import (
    decision_objects,
    object_attention,
    object_audit,
    object_candidates,
    object_profiles,
    object_relations,
)
from thoth.adapters.storage.transaction import (
    SqliteAtomicUnitOfWork,
    read_connection,
    write_connection,
)
from thoth.domain.decision_object_full import (
    DecisionObjectRecord,
    ObjectAttentionRecord,
    ObjectAuditRecord,
    ObjectCandidateRecord,
    ObjectProfileRecord,
    ObjectRelationRecord,
)
from thoth.domain.resource_scope import ResourceScopeError, current_resource_uses
from thoth.ports.decision_object import DecisionObjectStorePort
from thoth.ports.resource_scope import ResourceRecordAccessPort


def dump(value: object) -> str:
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS).decode()


def load(value: object) -> dict[str, object]:
    raw = cast(object, orjson.loads(str(value)))
    if not isinstance(raw, dict):
        raise ValueError("stored object payload is not an object")
    return {str(key): child for key, child in cast(dict[object, object], raw).items()}


class SqliteDecisionObjectStore(DecisionObjectStorePort):
    def __init__(
        self, engine: Engine, resource_access: ResourceRecordAccessPort | None = None
    ) -> None:
        self._engine = engine
        self._access = resource_access

    def _seal(self, project: str, digest: str, refs: tuple[str, ...] = ()) -> None:
        if self._access is None:
            return
        self._access.require_reads(project, refs)
        parents = (
            *refs,
            *(
                use.resource_ref
                for use in current_resource_uses() or ()
                if use.project_id == project and use.capability == "READ"
            ),
        )
        self._access.record_control_lineage(project, digest, parents)

    def _owner(self, project: str, object_id: str) -> str:
        record = self.read_object(project, object_id, None)
        if record is None:
            raise ResourceScopeError("RESOURCE_SCOPE_UNKNOWN")
        return f"revision:{record.revision_digest}"

    def _require_control(self, project: str, digest: str) -> None:
        if self._access is not None:
            self._access.require_read(project, f"control:{digest}")

    def _visible_control(self, project: str, digest: str) -> bool:
        return self._access is None or self._access.may_read(project, f"control:{digest}")

    def put_profile(self, value: ObjectProfileRecord) -> None:
        with write_connection(self._engine) as connection:
            connection.execute(
                sqlite_insert(object_profiles)
                .values(
                    profile_ref=value.profile_ref,
                    version=value.version,
                    content_json=dump(value.model_dump(mode="json")),
                    enabled=int(value.enabled),
                    profile_digest=value.profile_digest,
                )
                .on_conflict_do_nothing(index_elements=["profile_ref", "version"])
            )

    def list_profiles(self, enabled_only: bool) -> tuple[ObjectProfileRecord, ...]:
        statement = select(object_profiles)
        if enabled_only:
            statement = statement.where(object_profiles.c.enabled == 1)
        with read_connection(self._engine) as connection:
            rows = connection.execute(
                statement.order_by(object_profiles.c.profile_ref, object_profiles.c.version)
            ).mappings()
            return tuple(
                ObjectProfileRecord.model_validate(load(row["content_json"])) for row in rows
            )

    def read_profile(self, profile_ref: str, version: int | None) -> ObjectProfileRecord | None:
        statement = select(object_profiles).where(object_profiles.c.profile_ref == profile_ref)
        if version is None:
            statement = statement.order_by(object_profiles.c.version.desc()).limit(1)
        else:
            statement = statement.where(object_profiles.c.version == version)
        with read_connection(self._engine) as connection:
            row = connection.execute(statement).mappings().first()
        return (
            None if row is None else ObjectProfileRecord.model_validate(load(row["content_json"]))
        )

    def add_candidate(self, value: ObjectCandidateRecord) -> None:
        with (
            SqliteAtomicUnitOfWork(self._engine).transaction(),
            write_connection(self._engine) as connection,
        ):
            connection.execute(
                insert(object_candidates).values(
                    candidate_id=value.candidate_id,
                    project_id=value.project_id,
                    thread_id=value.thread_id,
                    content_json=dump(value.model_dump(mode="json")),
                    candidate_digest=value.candidate_digest,
                    created_at=value.created_at.isoformat(),
                )
            )
            self._seal(value.project_id, value.candidate_digest, value.trigger_evidence_refs)

    def list_candidates(
        self, project_id: str, thread_id: str | None
    ) -> tuple[ObjectCandidateRecord, ...]:
        statement = select(object_candidates).where(object_candidates.c.project_id == project_id)
        if thread_id is not None:
            statement = statement.where(object_candidates.c.thread_id == thread_id)
        with read_connection(self._engine) as connection:
            rows = connection.execute(statement.order_by(object_candidates.c.created_at)).mappings()
            values = tuple(
                ObjectCandidateRecord.model_validate(load(row["content_json"])) for row in rows
            )
        return tuple(
            value for value in values if self._visible_control(project_id, value.candidate_digest)
        )

    def read_candidate(self, candidate_id: str) -> ObjectCandidateRecord | None:
        with read_connection(self._engine) as connection:
            row = (
                connection.execute(
                    select(object_candidates).where(
                        object_candidates.c.candidate_id == candidate_id
                    )
                )
                .mappings()
                .first()
            )
        value = (
            None if row is None else ObjectCandidateRecord.model_validate(load(row["content_json"]))
        )
        if value is not None:
            self._require_control(value.project_id, value.candidate_digest)
        return value

    def add_object(self, value: DecisionObjectRecord) -> None:
        with write_connection(self._engine) as connection:
            connection.execute(
                insert(decision_objects).values(
                    object_revision_id=value.object_revision_id,
                    object_id=value.object_id,
                    project_id=value.project_id,
                    thread_id=value.thread_id,
                    content_json=dump(value.model_dump(mode="json")),
                    revision_digest=value.revision_digest,
                    supersedes_revision_digest=value.supersedes_revision_digest,
                    created_at=value.created_at.isoformat(),
                )
            )

    def list_objects(self, project_id: str) -> tuple[DecisionObjectRecord, ...]:
        with read_connection(self._engine) as connection:
            rows = connection.execute(
                select(decision_objects)
                .where(decision_objects.c.project_id == project_id)
                .order_by(decision_objects.c.created_at)
            ).mappings()
            values = tuple(
                DecisionObjectRecord.model_validate(load(row["content_json"])) for row in rows
            )
        latest: dict[str, DecisionObjectRecord] = {}
        for value in values:
            latest[value.object_id] = value
        return tuple(
            latest[key]
            for key in sorted(latest)
            if self._access is None
            or self._access.may_read_revision(project_id, latest[key].revision_digest)
        )

    def read_object(
        self, project_id: str, object_id: str, revision_digest: str | None
    ) -> DecisionObjectRecord | None:
        statement = select(decision_objects).where(
            decision_objects.c.project_id == project_id,
            decision_objects.c.object_id == object_id,
        )
        if revision_digest is None:
            statement = statement.order_by(decision_objects.c.created_at.desc()).limit(1)
        else:
            statement = statement.where(decision_objects.c.revision_digest == revision_digest)
        with read_connection(self._engine) as connection:
            row = connection.execute(statement).mappings().first()
        value = (
            None if row is None else DecisionObjectRecord.model_validate(load(row["content_json"]))
        )
        if value is not None and self._access is not None:
            self._access.require_revision(project_id, value.revision_digest)
        return value

    def add_relation(self, value: ObjectRelationRecord) -> None:
        with (
            SqliteAtomicUnitOfWork(self._engine).transaction(),
            write_connection(self._engine) as connection,
        ):
            connection.execute(
                insert(object_relations).values(
                    relation_revision_id=value.relation_revision_id,
                    relation_id=value.relation_id,
                    project_id=value.project_id,
                    source_object_id=value.source_object_id,
                    content_json=dump(value.model_dump(mode="json")),
                    revision_digest=value.revision_digest,
                    created_at=value.created_at.isoformat(),
                )
            )
            if self._access is not None:
                self._seal(
                    value.project_id,
                    value.revision_digest,
                    (self._owner(value.project_id, value.source_object_id), *value.evidence_refs),
                )

    def list_relations(self, project_id: str, object_id: str) -> tuple[ObjectRelationRecord, ...]:
        with read_connection(self._engine) as connection:
            rows = connection.execute(
                select(object_relations)
                .where(
                    object_relations.c.project_id == project_id,
                    object_relations.c.source_object_id == object_id,
                )
                .order_by(object_relations.c.created_at)
            ).mappings()
            values = tuple(
                ObjectRelationRecord.model_validate(load(row["content_json"])) for row in rows
            )
        latest: dict[str, ObjectRelationRecord] = {}
        for value in values:
            latest[value.relation_id] = value
        return tuple(
            latest[key]
            for key in sorted(latest)
            if self._visible_control(project_id, latest[key].revision_digest)
        )

    def read_relation(self, relation_id: str) -> ObjectRelationRecord | None:
        with read_connection(self._engine) as connection:
            row = (
                connection.execute(
                    select(object_relations)
                    .where(object_relations.c.relation_id == relation_id)
                    .order_by(object_relations.c.created_at.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
        value = (
            None if row is None else ObjectRelationRecord.model_validate(load(row["content_json"]))
        )
        if value is not None:
            self._require_control(value.project_id, value.revision_digest)
        return value

    def add_attention(self, value: ObjectAttentionRecord) -> None:
        with (
            SqliteAtomicUnitOfWork(self._engine).transaction(),
            write_connection(self._engine) as connection,
        ):
            connection.execute(
                insert(object_attention).values(
                    attention_revision_id=value.attention_revision_id,
                    attention_id=value.attention_id,
                    project_id=value.project_id,
                    object_id=value.object_id,
                    content_json=dump(value.model_dump(mode="json")),
                    revision_digest=value.revision_digest,
                    created_at=value.created_at.isoformat(),
                )
            )
            if self._access is not None:
                self._seal(
                    value.project_id,
                    value.revision_digest,
                    (self._owner(value.project_id, value.object_id),),
                )

    def list_attention(
        self, project_id: str, object_id: str | None
    ) -> tuple[ObjectAttentionRecord, ...]:
        statement = select(object_attention).where(object_attention.c.project_id == project_id)
        if object_id is not None:
            statement = statement.where(object_attention.c.object_id == object_id)
        with read_connection(self._engine) as connection:
            rows = connection.execute(statement.order_by(object_attention.c.created_at)).mappings()
            values = tuple(
                ObjectAttentionRecord.model_validate(load(row["content_json"])) for row in rows
            )
        latest: dict[str, ObjectAttentionRecord] = {}
        for value in values:
            latest[value.attention_id] = value
        return tuple(
            latest[key]
            for key in sorted(latest)
            if self._visible_control(project_id, latest[key].revision_digest)
        )

    def read_attention(self, attention_id: str) -> ObjectAttentionRecord | None:
        with read_connection(self._engine) as connection:
            row = (
                connection.execute(
                    select(object_attention)
                    .where(object_attention.c.attention_id == attention_id)
                    .order_by(object_attention.c.created_at.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
        value = (
            None if row is None else ObjectAttentionRecord.model_validate(load(row["content_json"]))
        )
        if value is not None:
            self._require_control(value.project_id, value.revision_digest)
        return value

    def append_audit(self, value: ObjectAuditRecord) -> None:
        with (
            SqliteAtomicUnitOfWork(self._engine).transaction(),
            write_connection(self._engine) as connection,
        ):
            connection.execute(
                insert(object_audit).values(
                    audit_id=value.audit_id,
                    project_id=value.project_id,
                    object_id=value.object_id,
                    event_type=value.event_type,
                    payload_json=dump(value.payload),
                    event_digest=value.event_digest,
                    created_at=value.created_at.isoformat(),
                )
            )
            if self._access is not None:
                self._seal(
                    value.project_id,
                    value.event_digest,
                    (self._owner(value.project_id, value.object_id),),
                )

    def list_audit(self, project_id: str, object_id: str) -> tuple[ObjectAuditRecord, ...]:
        with read_connection(self._engine) as connection:
            rows = connection.execute(
                select(object_audit)
                .where(
                    object_audit.c.project_id == project_id,
                    object_audit.c.object_id == object_id,
                )
                .order_by(object_audit.c.created_at)
            ).mappings()
            values = tuple(
                ObjectAuditRecord(
                    audit_id=str(row["audit_id"]),
                    project_id=str(row["project_id"]),
                    object_id=str(row["object_id"]),
                    event_type=str(row["event_type"]),
                    payload=load(row["payload_json"]),
                    event_digest=str(row["event_digest"]),
                    created_at=datetime.fromisoformat(str(row["created_at"])),
                )
                for row in rows
            )
        return tuple(
            value for value in values if self._visible_control(project_id, value.event_digest)
        )
