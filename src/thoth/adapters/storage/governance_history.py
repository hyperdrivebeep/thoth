"""Append the immutable owner on the caller's existing SQLite connection."""

from datetime import UTC, datetime
from typing import cast

from sqlalchemy import Connection, Engine, and_, insert, select, update
from sqlalchemy.engine import RowMapping

from thoth.adapters.storage.governance_history_schema import governance_heads as heads
from thoth.adapters.storage.governance_history_schema import governance_revisions as revisions
from thoth.adapters.storage.transaction import read_connection
from thoth.domain.auth import current_authenticated_actor
from thoth.domain.canonical import canonical_payload
from thoth.domain.governance_revision import (
    GovernanceKind,
    GovernanceReceipt,
    GovernanceRevision,
    GovernanceRevisionBody,
    Projection,
    seal_governance,
)
from thoth.ports.governance_history import GovernanceHistoryPort


def key(project_id: str, kind: GovernanceKind, record_id: str):
    return and_(
        heads.c.project_id == project_id,
        heads.c.record_kind == kind,
        heads.c.record_id == record_id,
    )


class SqliteGovernanceHistory(GovernanceHistoryPort):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def read_current(
        self, project_id: str, kind: GovernanceKind, record_id: str
    ) -> GovernanceRevision | None:
        with read_connection(self._engine) as connection:
            return self.current_on(connection, project_id, kind, record_id)

    @classmethod
    def current_on(
        cls, connection: Connection, project_id: str, kind: GovernanceKind, record_id: str
    ) -> GovernanceRevision | None:
        head = (
            connection.execute(select(heads).where(key(project_id, kind, record_id)))
            .mappings()
            .first()
        )
        if head is None:
            return None
        row = (
            connection.execute(
                select(revisions).where(
                    revisions.c.project_id == project_id,
                    revisions.c.record_kind == kind,
                    revisions.c.record_id == record_id,
                    revisions.c.revision == head["revision"],
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ValueError("GOVERNANCE_HEAD_RECORD_MISSING")
        record = cls._decode_record(row)
        if record.record_digest != head["record_digest"] or record.revision != head["revision"]:
            raise ValueError("GOVERNANCE_HEAD_RECEIPT_MISMATCH")
        return record

    @staticmethod
    def _decode_record(row: RowMapping) -> GovernanceRevision:
        try:
            record = GovernanceRevision.model_validate_json(str(row["content_json"]))
            receipt = GovernanceReceipt.model_validate_json(str(row["receipt_json"]))
        except ValueError as exc:
            raise ValueError("GOVERNANCE_PERSISTED_RECORD_INVALID") from exc
        if (
            record.record_digest != row["record_digest"]
            or record.project_id != row["project_id"]
            or record.record_kind != row["record_kind"]
            or record.record_id != row["record_id"]
            or record.revision != row["revision"]
            or receipt.receipt_digest != row["receipt_digest"]
            or receipt.after_digest != record.record_digest
            or receipt.before_digest != record.previous_digest
            or receipt.project_id != record.project_id
            or receipt.record_kind != record.record_kind
            or receipt.record_id != record.record_id
            or receipt.origin != record.origin
            or receipt.actor_ref != record.actor_ref
            or receipt.recorded_at != record.recorded_at
        ):
            raise ValueError("GOVERNANCE_HEAD_RECEIPT_MISMATCH")
        return record

    def history(
        self, project_id: str, kind: GovernanceKind, record_id: str
    ) -> tuple[GovernanceRevision, ...]:
        with read_connection(self._engine) as connection:
            rows = (
                connection.execute(
                    select(revisions)
                    .where(
                        revisions.c.project_id == project_id,
                        revisions.c.record_kind == kind,
                        revisions.c.record_id == record_id,
                    )
                    .order_by(revisions.c.revision)
                )
                .mappings()
                .all()
            )
            values = tuple(self._decode_record(row) for row in rows)
            head = self.current_on(connection, project_id, kind, record_id)
        if (head is None) != (not values) or (
            head and values[-1].record_digest != head.record_digest
        ):
            raise ValueError("GOVERNANCE_HISTORY_HEAD_MISMATCH")
        for index, value in enumerate(values):
            if value.revision != index + 1 or value.previous_digest != (
                None if index == 0 else values[index - 1].record_digest
            ):
                raise ValueError("GOVERNANCE_HISTORY_CHAIN_MISMATCH")
        return values

    @classmethod
    def assert_current(
        cls, connection: Connection, kind: GovernanceKind, projection: dict[str, object]
    ) -> GovernanceRevision:
        identifier = {
            "PROJECT": "project_id",
            "ROLE": "role_assignment_id",
            "SOURCE_BINDING": "binding_id",
        }[kind]
        current = cls.current_on(
            connection, str(projection["project_id"]), kind, str(projection[identifier])
        )
        if current is None or canonical_payload(current.projection) != canonical_payload(
            projection
        ):
            raise ValueError("GOVERNANCE_PROJECTION_BASIS_MISMATCH")
        return current

    @classmethod
    def stage(
        cls,
        connection: Connection,
        kind: GovernanceKind,
        projection: dict[str, object],
        *,
        before: dict[str, object] | None = None,
    ) -> GovernanceRevision:
        identifier = {
            "PROJECT": "project_id",
            "ROLE": "role_assignment_id",
            "SOURCE_BINDING": "binding_id",
        }[kind]
        project_id, record_id = str(projection["project_id"]), str(projection[identifier])
        previous = cls.current_on(connection, project_id, kind, record_id)
        if before is None:
            if previous is not None:
                raise ValueError("GOVERNANCE_RECORD_ALREADY_EXISTS")
        else:
            if previous is None or canonical_payload(previous.projection) != canonical_payload(
                before
            ):
                raise ValueError("GOVERNANCE_PREDECESSOR_BASIS_MISMATCH")
            if canonical_payload(before) == canonical_payload(projection):
                return previous
        related = {
            str(row["record_kind"]) + ":" + str(row["record_id"]): str(row["record_digest"])
            for row in connection.execute(
                select(heads).where(heads.c.project_id == project_id)
            ).mappings()
            if (row["record_kind"], row["record_id"]) != (kind, record_id)
        }
        actor = current_authenticated_actor()
        record, receipt = seal_governance(
            GovernanceRevisionBody(
                project_id=project_id,
                record_kind=kind,
                record_id=record_id,
                revision=1 if previous is None else previous.revision + 1,
                previous_digest=None if previous is None else previous.record_digest,
                origin="RECORDED",
                projection=cast(Projection, projection),
                related_refs=related,
                actor_ref="local:operator" if actor is None else actor.actor_id,
                recorded_at=datetime.now(UTC),
            )
        )
        cls._insert_record(connection, record, receipt)
        values = {"revision": record.revision, "record_digest": record.record_digest}
        if previous is None:
            connection.execute(
                insert(heads).values(
                    project_id=project_id, record_kind=kind, record_id=record_id, **values
                )
            )
        elif (
            connection.execute(
                update(heads)
                .where(
                    key(project_id, kind, record_id),
                    heads.c.record_digest == previous.record_digest,
                    heads.c.revision == previous.revision,
                )
                .values(**values)
            ).rowcount
            != 1
        ):
            raise ValueError("GOVERNANCE_HEAD_CAS_FAILED")
        return record

    @staticmethod
    def _insert_record(
        connection: Connection, record: GovernanceRevision, receipt: GovernanceReceipt
    ) -> None:
        connection.execute(
            insert(revisions).values(
                project_id=record.project_id,
                record_kind=record.record_kind,
                record_id=record.record_id,
                revision=record.revision,
                record_digest=record.record_digest,
                content_json=canonical_payload(record).decode(),
                receipt_digest=receipt.receipt_digest,
                receipt_json=canonical_payload(receipt).decode(),
            )
        )
