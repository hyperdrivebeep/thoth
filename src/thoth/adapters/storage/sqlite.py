from __future__ import annotations

from collections.abc import Generator, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import cast

import orjson
from sqlalchemy import (
    Connection,
    Engine,
    create_engine,
    delete,
    insert,
    inspect,
    select,
    text,
    update,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from thoth.adapters.storage.schema import (
    dependency_states,
    entity_snapshots,
    receipts,
    revision_parents,
    semantic_revisions,
    structural_nodes,
    working_heads,
)
from thoth.adapters.storage.transaction import (
    SqliteAtomicUnitOfWork,
    read_connection,
    write_connection,
)
from thoth.domain.canonical import canonical_payload
from thoth.domain.enums import EntityType, ImpactStatus
from thoth.domain.ids import ProjectId, Sha256
from thoth.domain.migration import MigrationFailure, MigrationFailureCode
from thoth.domain.receipt import Receipt
from thoth.domain.revision import EntitySnapshot, ImpactPropagationPlan, SemanticRevision
from thoth.ports.ledger import LedgerProjectionPort, LedgerTransactionPort, ReceiptProjectionPort


def _json_dump(value: object) -> str:
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS).decode("utf-8")


def _json_load(value: str) -> object:
    return orjson.loads(value)


class SqliteLedgerTransaction:
    def __init__(
        self,
        connection: Connection,
        projections: tuple[LedgerProjectionPort, ...] = (),
        receipt_projections: tuple[ReceiptProjectionPort, ...] = (),
    ) -> None:
        self._connection = connection
        self._projections = projections
        self._receipt_projections = receipt_projections

    def insert_snapshot(self, snapshot: EntitySnapshot) -> None:
        self._connection.execute(
            insert(entity_snapshots).values(
                snapshot_id=snapshot.snapshot_id,
                project_id=snapshot.project_id,
                entity_type=snapshot.entity_type.value,
                entity_id=snapshot.entity_id,
                schema_version=snapshot.schema_version,
                content_json=canonical_payload(snapshot.content).decode("utf-8"),
                content_digest=snapshot.content_digest,
            )
        )

    def insert_revision(self, revision: SemanticRevision) -> None:
        self._connection.execute(
            insert(semantic_revisions).values(
                revision_id=revision.revision_id,
                project_id=revision.project_id,
                entity_type=revision.entity_type.value,
                entity_id=revision.entity_id,
                snapshot_id=revision.snapshot_id,
                actor_json=_json_dump(revision.actor.model_dump(mode="json")),
                reason=revision.reason,
                evidence_refs_json=_json_dump(revision.evidence_refs),
                affected_refs_json=_json_dump(revision.affected_refs),
                revision_digest=revision.revision_digest,
                created_at=revision.created_at.isoformat(),
                schema_version=revision.schema_version,
            )
        )
        for ordinal, parent_digest in enumerate(revision.parent_revision_digests):
            self._connection.execute(
                insert(revision_parents).values(
                    revision_id=revision.revision_id,
                    ordinal=ordinal,
                    parent_digest=parent_digest,
                )
            )
        for projection in self._projections:
            projection.stage_revision(revision)

    def get_head(self, project_id: ProjectId, aggregate_key: str) -> Sha256 | None:
        row = self._connection.execute(
            select(working_heads.c.revision_digest).where(
                working_heads.c.project_id == project_id,
                working_heads.c.aggregate_key == aggregate_key,
            )
        ).first()
        return None if row is None else str(row.revision_digest)

    def get_heads(self, project_id: ProjectId) -> dict[str, Sha256]:
        rows = self._connection.execute(
            select(working_heads.c.aggregate_key, working_heads.c.revision_digest).where(
                working_heads.c.project_id == project_id
            )
        ).all()
        return {str(row.aggregate_key): str(row.revision_digest) for row in rows}

    def set_head(self, project_id: ProjectId, aggregate_key: str, revision_digest: Sha256) -> None:
        existing = self.get_head(project_id, aggregate_key)
        if existing is None:
            self._connection.execute(
                insert(working_heads).values(
                    project_id=project_id,
                    aggregate_key=aggregate_key,
                    revision_digest=revision_digest,
                    updated_at=text("CURRENT_TIMESTAMP"),
                )
            )
        else:
            self._connection.execute(
                update(working_heads)
                .where(
                    working_heads.c.project_id == project_id,
                    working_heads.c.aggregate_key == aggregate_key,
                )
                .values(revision_digest=revision_digest, updated_at=text("CURRENT_TIMESTAMP"))
            )

    def insert_receipt(self, receipt: Receipt) -> None:
        self._connection.execute(
            insert(receipts).values(
                receipt_id=receipt.receipt_id,
                project_id=receipt.project_id,
                receipt_type=receipt.receipt_type.value,
                payload_json=_json_dump(receipt.model_dump(mode="json")),
                receipt_digest=receipt.receipt_digest,
                previous_transition_digest=receipt.previous_transition_digest,
                created_at=receipt.recorded_at.isoformat(),
            )
        )
        for projection in self._receipt_projections:
            projection.stage_receipt(receipt)

    def apply_impact_plan(
        self,
        project_id: ProjectId,
        plan: ImpactPropagationPlan,
        *,
        caused_by_revision: Sha256,
        updated_at: str,
    ) -> None:
        states = {
            **{reference: ImpactStatus.STALE for reference in plan.stale_refs},
            **{reference: ImpactStatus.INVALIDATED for reference in plan.invalidated_refs},
            **{
                reference: ImpactStatus.RECALCULATION_REQUIRED
                for reference in plan.recalculate_refs
            },
        }
        for entity_ref, status in states.items():
            statement = sqlite_insert(dependency_states).values(
                project_id=project_id,
                entity_ref=entity_ref,
                status=status.value,
                caused_by_revision=caused_by_revision,
                updated_at=updated_at,
            )
            self._connection.execute(
                statement.on_conflict_do_update(
                    index_elements=["project_id", "entity_ref"],
                    set_={
                        "status": status.value,
                        "caused_by_revision": caused_by_revision,
                        "updated_at": updated_at,
                    },
                )
            )


class SqliteLedger:
    def __init__(self, database_path: Path | str) -> None:
        path = Path(database_path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._engine: Engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
        self._projections: dict[str, LedgerProjectionPort] = {}
        self._receipt_projections: dict[str, ReceiptProjectionPort] = {}

    def register_projection(self, name: str, projection: LedgerProjectionPort) -> None:
        self._projections[name] = projection

    def register_receipt_projection(self, name: str, projection: ReceiptProjectionPort) -> None:
        self._receipt_projections[name] = projection

    def initialize(self) -> None:
        tables = set(inspect(self._engine).get_table_names())
        required = {
            "alembic_version",
            "entity_snapshots",
            "semantic_revisions",
            "working_heads",
            "structural_fts",
        }
        missing = required - tables
        if missing:
            raise MigrationFailure(
                MigrationFailureCode.SCHEMA_NOT_READY,
                "schema migration is required before ledger initialization: "
                + ", ".join(sorted(missing)),
            )
        with read_connection(self._engine) as connection:
            heads = tuple(
                str(row[0])
                for row in connection.exec_driver_sql(
                    "SELECT version_num FROM alembic_version"
                ).all()
            )
        if len(heads) != 1:
            raise MigrationFailure(
                MigrationFailureCode.DATABASE_HEAD_COUNT_INVALID,
                f"database migration state requires exactly one head; found {len(heads)}",
            )

    @property
    def engine(self) -> Engine:
        return self._engine

    @contextmanager
    def transaction(self) -> Generator[LedgerTransactionPort, None, None]:
        with (
            SqliteAtomicUnitOfWork(self._engine, immediate=True).transaction(),
            write_connection(self._engine) as connection,
        ):
            yield SqliteLedgerTransaction(
                connection,
                tuple(self._projections.values()),
                tuple(self._receipt_projections.values()),
            )

    def read_heads(self, project_id: ProjectId) -> Mapping[str, Sha256]:
        with read_connection(self._engine) as connection:
            rows = connection.execute(
                select(working_heads.c.aggregate_key, working_heads.c.revision_digest).where(
                    working_heads.c.project_id == project_id
                )
            ).all()
        return {str(row.aggregate_key): str(row.revision_digest) for row in rows}

    def read_revisions(
        self, project_id: ProjectId, entity_type: str, entity_id: str
    ) -> tuple[SemanticRevision, ...]:
        statement = (
            select(semantic_revisions)
            .where(
                semantic_revisions.c.project_id == project_id,
                semantic_revisions.c.entity_type == entity_type,
                semantic_revisions.c.entity_id == entity_id,
            )
            .order_by(semantic_revisions.c.created_at, semantic_revisions.c.revision_id)
        )
        revisions: list[SemanticRevision] = []
        with read_connection(self._engine) as connection:
            for raw_row in connection.execute(statement).mappings():
                row = raw_row
                parent_rows = connection.execute(
                    select(revision_parents.c.parent_digest)
                    .where(revision_parents.c.revision_id == row["revision_id"])
                    .order_by(revision_parents.c.ordinal)
                ).all()
                payload = dict(row)
                payload["actor"] = _json_load(str(payload.pop("actor_json")))
                payload["evidence_refs"] = _json_load(str(payload.pop("evidence_refs_json")))
                payload["affected_refs"] = _json_load(str(payload.pop("affected_refs_json")))
                payload["parent_revision_digests"] = tuple(
                    str(parent.parent_digest) for parent in parent_rows
                )
                revisions.append(SemanticRevision.model_validate(payload))
        return tuple(revisions)

    def read_revision_by_digest(
        self, project_id: ProjectId, revision_digest: Sha256
    ) -> SemanticRevision | None:
        with read_connection(self._engine) as connection:
            row = (
                connection.execute(
                    select(semantic_revisions).where(
                        semantic_revisions.c.project_id == project_id,
                        semantic_revisions.c.revision_digest == revision_digest,
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            parent_rows = connection.execute(
                select(revision_parents.c.parent_digest)
                .where(revision_parents.c.revision_id == row["revision_id"])
                .order_by(revision_parents.c.ordinal)
            ).all()
        payload = dict(row)
        payload["actor"] = _json_load(str(payload.pop("actor_json")))
        payload["evidence_refs"] = _json_load(str(payload.pop("evidence_refs_json")))
        payload["affected_refs"] = _json_load(str(payload.pop("affected_refs_json")))
        payload["parent_revision_digests"] = tuple(
            str(parent.parent_digest) for parent in parent_rows
        )
        return SemanticRevision.model_validate(payload)

    def read_revision_by_id(
        self, project_id: ProjectId, revision_id: str
    ) -> SemanticRevision | None:
        with read_connection(self._engine) as connection:
            digest = connection.execute(
                select(semantic_revisions.c.revision_digest).where(
                    semantic_revisions.c.project_id == project_id,
                    semantic_revisions.c.revision_id == revision_id,
                )
            ).scalar_one_or_none()
        return None if digest is None else self.read_revision_by_digest(project_id, str(digest))

    def read_receipts(self, project_id: ProjectId) -> tuple[Receipt, ...]:
        statement = (
            select(receipts.c.payload_json)
            .where(receipts.c.project_id == project_id)
            .order_by(receipts.c.created_at, receipts.c.receipt_id)
        )
        with read_connection(self._engine) as connection:
            values = connection.execute(statement).scalars().all()
        return tuple(Receipt.model_validate(_json_load(str(value))) for value in values)

    def read_snapshot(self, snapshot_id: str) -> EntitySnapshot | None:
        with read_connection(self._engine) as connection:
            row = (
                connection.execute(
                    select(entity_snapshots).where(entity_snapshots.c.snapshot_id == snapshot_id)
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        content = _json_load(str(row["content_json"]))
        if not isinstance(content, dict):
            raise ValueError("stored snapshot content is not an object")
        content_mapping = cast(dict[object, object], content)
        return EntitySnapshot(
            snapshot_id=str(row["snapshot_id"]),
            project_id=str(row["project_id"]),
            entity_type=EntityType(str(row["entity_type"])),
            entity_id=str(row["entity_id"]),
            schema_version=str(row["schema_version"]),
            content={str(key): value for key, value in content_mapping.items()},
            content_digest=str(row["content_digest"]),
        )

    def read_dependency_states(self, project_id: ProjectId) -> dict[str, ImpactStatus]:
        with read_connection(self._engine) as connection:
            rows = connection.execute(
                select(dependency_states.c.entity_ref, dependency_states.c.status).where(
                    dependency_states.c.project_id == project_id
                )
            ).all()
        return {str(row.entity_ref): ImpactStatus(str(row.status)) for row in rows}

    def index_text(self, project_id: ProjectId, node_id: str, artifact_id: str, value: str) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                insert(structural_nodes).values(
                    node_id=node_id,
                    project_id=project_id,
                    artifact_id=artifact_id,
                    text=value,
                    locator_json="{}",
                )
            )
            connection.exec_driver_sql(
                "INSERT INTO structural_fts(node_id, project_id, artifact_id, text) "
                "VALUES (?, ?, ?, ?)",
                (node_id, project_id, artifact_id, value),
            )

    def delete_indexed_text(self, node_id: str) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                delete(structural_nodes).where(structural_nodes.c.node_id == node_id)
            )
            connection.exec_driver_sql("DELETE FROM structural_fts WHERE node_id = ?", (node_id,))

    def search_text(self, project_id: ProjectId, query: str) -> Iterator[tuple[str, str]]:
        with read_connection(self._engine) as connection:
            if len(query) < 3:
                rows = connection.execute(
                    select(structural_nodes.c.node_id, structural_nodes.c.text)
                    .where(
                        structural_nodes.c.project_id == project_id,
                        structural_nodes.c.text.contains(query),
                    )
                    .order_by(structural_nodes.c.node_id)
                ).all()
            else:
                rows = connection.exec_driver_sql(
                    "SELECT node_id, text FROM structural_fts "
                    "WHERE project_id = ? AND structural_fts MATCH ? ORDER BY rank",
                    (project_id, query),
                ).all()
        yield from ((str(row.node_id), str(row.text)) for row in rows)

    def close(self) -> None:
        self._engine.dispose()


def remove_database(path: Path) -> None:
    """Test helper with an explicit narrow target."""
    resolved = path.resolve()
    if resolved.is_file():
        resolved.unlink()
