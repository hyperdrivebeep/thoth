"""Scope history, current head and transition receipt share one atomic write domain."""

from sqlalchemy import Engine, and_, insert, select, update
from sqlalchemy.exc import IntegrityError

from thoth.adapters.storage.resource_scope_schema import (
    resource_scope_heads as heads,
)
from thoth.adapters.storage.resource_scope_schema import (
    resource_scope_history as history,
)
from thoth.adapters.storage.resource_scope_schema import (
    resource_scope_receipts as receipts,
)
from thoth.adapters.storage.transaction import read_connection, write_connection
from thoth.domain.resource_scope import (
    ResourceScopeError,
    ResourceScopeReceipt,
    ResourceScopeRecord,
)
from thoth.ports.resource_scope import ResourceScopeStorePort


class SqliteResourceScopeStore(ResourceScopeStorePort):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def read(self, project_id: str, resource_ref: str) -> ResourceScopeRecord | None:
        with read_connection(self._engine) as connection:
            row = (
                connection.execute(
                    select(
                        history.c.content_json,
                        heads.c.record_digest,
                        heads.c.revision,
                        history.c.record_digest.label("history_digest"),
                    )
                    .select_from(
                        heads.outerjoin(
                            history,
                            and_(
                                history.c.project_id == heads.c.project_id,
                                history.c.resource_ref == heads.c.resource_ref,
                                history.c.revision == heads.c.revision,
                            ),
                        )
                    )
                    .where(heads.c.project_id == project_id, heads.c.resource_ref == resource_ref)
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            try:
                value = ResourceScopeRecord.model_validate_json(str(row["content_json"]))
            except ValueError as exc:
                raise ResourceScopeError("RESOURCE_SCOPE_HEAD_MISMATCH") from exc
            if (
                value.project_id != project_id
                or value.resource_ref != resource_ref
                or value.revision != row["revision"]
                or value.record_digest != str(row["record_digest"])
                or value.record_digest != str(row["history_digest"])
            ):
                raise ResourceScopeError("RESOURCE_SCOPE_HEAD_MISMATCH")
            stored = (
                connection.execute(
                    select(receipts.c.content_json, receipts.c.receipt_digest).where(
                        receipts.c.project_id == project_id,
                        receipts.c.receipt_id == value.receipt_ref,
                    )
                )
                .mappings()
                .first()
            )
            if stored is None:
                raise ResourceScopeError("RESOURCE_SCOPE_RECEIPT_MISSING")
            try:
                receipt = ResourceScopeReceipt.model_validate_json(str(stored["content_json"]))
            except ValueError as exc:
                raise ResourceScopeError("RESOURCE_SCOPE_RECEIPT_MISMATCH") from exc
            if (
                receipt.receipt_digest != str(stored["receipt_digest"])
                or receipt.after_digest != value.record_digest
                or receipt.before_digest != value.previous_digest
                or receipt.project_id != project_id
                or receipt.resource_ref != resource_ref
                or receipt.actor_ref != value.actor_ref
                or receipt.session_ref != value.session_ref
                or receipt.event_kind != value.event_kind
                or receipt.created_at != value.created_at
            ):
                raise ResourceScopeError("RESOURCE_SCOPE_RECEIPT_MISMATCH")
            return value

    def history(self, project_id: str, resource_ref: str) -> tuple[ResourceScopeRecord, ...]:
        with read_connection(self._engine) as connection:
            rows = (
                connection.execute(
                    select(history.c.content_json)
                    .where(
                        history.c.project_id == project_id, history.c.resource_ref == resource_ref
                    )
                    .order_by(history.c.revision)
                )
                .scalars()
                .all()
            )
        return tuple(ResourceScopeRecord.model_validate_json(str(row)) for row in rows)

    def append(
        self, value: ResourceScopeRecord, receipt: ResourceScopeReceipt, *, expected_revision: int
    ) -> None:
        if (
            value.revision != expected_revision + 1
            or receipt.project_id != value.project_id
            or receipt.resource_ref != value.resource_ref
            or receipt.receipt_id != value.receipt_ref
            or receipt.before_digest != value.previous_digest
            or receipt.after_digest != value.record_digest
        ):
            raise ResourceScopeError("RESOURCE_SCOPE_TRANSITION_BINDING_INVALID")
        try:
            with write_connection(self._engine) as connection:
                current = (
                    connection.execute(
                        select(heads).where(
                            heads.c.project_id == value.project_id,
                            heads.c.resource_ref == value.resource_ref,
                        )
                    )
                    .mappings()
                    .first()
                )
                if current is not None and str(current["record_digest"]) == value.record_digest:
                    stored = connection.execute(
                        select(receipts.c.content_json).where(
                            receipts.c.project_id == value.project_id,
                            receipts.c.receipt_id == receipt.receipt_id,
                        )
                    ).scalar_one_or_none()
                    if (
                        stored is None
                        or ResourceScopeReceipt.model_validate_json(str(stored)) != receipt
                    ):
                        raise ResourceScopeError("RESOURCE_SCOPE_RECEIPT_MISMATCH")
                    return
                if expected_revision == 0:
                    if current is not None:
                        raise ResourceScopeError("RESOURCE_SCOPE_REVISION_CONFLICT")
                    connection.execute(
                        insert(heads).values(
                            project_id=value.project_id,
                            resource_ref=value.resource_ref,
                            revision=value.revision,
                            record_digest=value.record_digest,
                        )
                    )
                else:
                    changed = connection.execute(
                        update(heads)
                        .where(
                            heads.c.project_id == value.project_id,
                            heads.c.resource_ref == value.resource_ref,
                            heads.c.revision == expected_revision,
                            heads.c.record_digest == value.previous_digest,
                        )
                        .values(revision=value.revision, record_digest=value.record_digest)
                    ).rowcount
                    if changed != 1:
                        raise ResourceScopeError("RESOURCE_SCOPE_REVISION_CONFLICT")
                connection.execute(
                    insert(history).values(
                        project_id=value.project_id,
                        resource_ref=value.resource_ref,
                        revision=value.revision,
                        record_digest=value.record_digest,
                        content_json=value.model_dump_json(),
                    )
                )
                connection.execute(
                    insert(receipts).values(
                        project_id=value.project_id,
                        receipt_id=receipt.receipt_id,
                        resource_ref=value.resource_ref,
                        receipt_digest=receipt.receipt_digest,
                        content_json=receipt.model_dump_json(),
                    )
                )
        except IntegrityError as exc:
            raise ResourceScopeError("RESOURCE_SCOPE_REVISION_CONFLICT") from exc
