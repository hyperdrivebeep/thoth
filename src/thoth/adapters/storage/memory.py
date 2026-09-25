from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar, cast

import orjson
from sqlalchemy import Engine, Table, delete, insert, select

from thoth.adapters.storage.schema import (
    memory_context_packs,
    memory_projections,
    memory_records,
    memory_revision_ledger,
    memory_transition_receipts,
)
from thoth.adapters.storage.transaction import read_connection, write_connection
from thoth.domain.base import DomainModel
from thoth.domain.enums import (
    MemoryKind,
    MemoryLifecycle,
    MemoryPayloadMode,
    RecallEligibility,
)
from thoth.domain.memory import (
    FullMemoryContextPack,
    FullMemoryRevision,
    MemoryProjection,
    MemoryRecord,
    MemoryTransitionReceipt,
)
from thoth.ports.memory import FullMemoryStorePort, MemoryStorePort

TRecord = TypeVar("TRecord", bound=DomainModel)


def _dump(value: DomainModel) -> str:
    return orjson.dumps(value.model_dump(mode="json"), option=orjson.OPT_SORT_KEYS).decode()


class SqliteMemoryStore(MemoryStorePort):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add(self, record: MemoryRecord) -> None:
        with write_connection(self._engine) as connection:
            connection.execute(
                insert(memory_records).values(
                    memory_id=record.memory_id,
                    project_id=record.project_id,
                    payload_mode=record.payload_mode.value,
                    kind=record.kind.value,
                    owner_revision_ref=record.owner_revision_ref,
                    source_ref=record.source_ref,
                    assertion=record.assertion,
                    recall_eligibility=record.recall_eligibility.value,
                    lifecycle=record.lifecycle.value,
                    revision_digest=record.revision_digest,
                )
            )

    def list(self, project_id: str | None = None) -> tuple[MemoryRecord, ...]:
        statement = select(memory_records).order_by(memory_records.c.memory_id)
        if project_id is not None:
            statement = statement.where(memory_records.c.project_id == project_id)
        with read_connection(self._engine) as connection:
            rows = connection.execute(statement).mappings().all()
        return tuple(
            MemoryRecord(
                memory_id=str(row["memory_id"]),
                project_id=str(row["project_id"]),
                payload_mode=MemoryPayloadMode(str(row["payload_mode"])),
                kind=MemoryKind(str(row["kind"])),
                owner_revision_ref=str(row["owner_revision_ref"]),
                source_ref=None if row["source_ref"] is None else str(row["source_ref"]),
                assertion=None if row["assertion"] is None else str(row["assertion"]),
                recall_eligibility=RecallEligibility(str(row["recall_eligibility"])),
                lifecycle=MemoryLifecycle(str(row["lifecycle"])),
                revision_digest=str(row["revision_digest"]),
            )
            for row in rows
        )


class SqliteFullMemoryStore(FullMemoryStorePort):
    def __init__(
        self,
        engine: Engine,
        *,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        self._engine = engine
        self._fault_injector = fault_injector

    def commit_transition(
        self,
        revision: FullMemoryRevision,
        receipt: MemoryTransitionReceipt,
    ) -> None:
        if self.read_by_source_memory_id(revision.project_id, revision.memory_id) is not None:
            return
        with write_connection(self._engine) as connection:
            connection.execute(
                insert(memory_revision_ledger).values(
                    memory_revision_id=revision.memory_revision_id,
                    memory_id=revision.memory_id,
                    project_id=revision.project_id,
                    transition=revision.transition.value,
                    owner_revision_ref=revision.owner_revision_ref,
                    content_json=_dump(revision),
                    revision_digest=revision.revision_digest,
                )
            )
            self._inject("after_revision")
            connection.execute(
                insert(memory_transition_receipts).values(
                    receipt_id=receipt.receipt_id,
                    project_id=receipt.project_id,
                    memory_revision_id=receipt.memory_revision_id,
                    content_json=_dump(receipt),
                    receipt_digest=receipt.receipt_digest,
                )
            )
            self._inject("after_receipt")

    def read_by_source_memory_id(
        self, project_id: str, memory_id: str
    ) -> FullMemoryRevision | None:
        with read_connection(self._engine) as connection:
            row = connection.execute(
                select(memory_revision_ledger.c.content_json).where(
                    memory_revision_ledger.c.project_id == project_id,
                    memory_revision_ledger.c.memory_id == memory_id,
                )
            ).first()
        return None if row is None else FullMemoryRevision.model_validate(orjson.loads(str(row[0])))

    def list_revisions(self, project_id: str) -> tuple[FullMemoryRevision, ...]:
        return self._list(memory_revision_ledger, project_id, FullMemoryRevision)

    def list_receipts(self, project_id: str) -> tuple[MemoryTransitionReceipt, ...]:
        return self._list(
            memory_transition_receipts,
            project_id,
            MemoryTransitionReceipt,
        )

    def put_context(self, context: FullMemoryContextPack) -> None:
        with write_connection(self._engine) as connection:
            connection.execute(
                insert(memory_context_packs).values(
                    context_pack_id=context.context_pack_id,
                    project_id=context.project_id,
                    thread_id=context.thread_id,
                    content_json=_dump(context),
                    query_digest=context.query_digest,
                )
            )

    def list_contexts(self, project_id: str) -> tuple[FullMemoryContextPack, ...]:
        return self._list(memory_context_packs, project_id, FullMemoryContextPack)

    def replace_projections(
        self, project_id: str, projections: tuple[MemoryProjection, ...]
    ) -> None:
        with write_connection(self._engine) as connection:
            connection.execute(
                delete(memory_projections).where(memory_projections.c.project_id == project_id)
            )
            for projection in projections:
                connection.execute(
                    insert(memory_projections).values(
                        projection_id=projection.projection_id,
                        project_id=projection.project_id,
                        projection_type=projection.projection_type,
                        content_json=_dump(projection),
                        rebuild_checkpoint=projection.rebuild_checkpoint,
                    )
                )

    def list_projections(self, project_id: str) -> tuple[MemoryProjection, ...]:
        return self._list(memory_projections, project_id, MemoryProjection)

    def _inject(self, step: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(step)

    def _list(
        self,
        table: Table,
        project_id: str,
        model: type[TRecord],
    ) -> tuple[TRecord, ...]:
        with read_connection(self._engine) as connection:
            rows = connection.execute(
                select(table.c.content_json).where(table.c.project_id == project_id)
            ).all()
        return tuple(model.model_validate(orjson.loads(str(cast(object, row[0])))) for row in rows)
