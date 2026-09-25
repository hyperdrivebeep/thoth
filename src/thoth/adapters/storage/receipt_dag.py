from __future__ import annotations

from typing import TypeVar, cast

import orjson
from sqlalchemy import Engine, Table, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from thoth.adapters.storage.schema import (
    receipt_dag_bundles,
    receipt_dag_edges,
    receipt_dag_manifests,
    receipt_dag_nodes,
    receipt_dag_verifications,
)
from thoth.domain.base import DomainModel
from thoth.domain.receipt_dag import (
    ReceiptBundle,
    ReceiptDagEdge,
    ReceiptDagManifest,
    ReceiptDagNode,
    ReceiptDagVerification,
)
from thoth.ports.receipt_dag import ReceiptDagStorePort

TRecord = TypeVar("TRecord", bound=DomainModel)


def _dump(value: object) -> str:
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS).decode()


class SqliteReceiptDagStore(ReceiptDagStorePort):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def put_node(self, value: ReceiptDagNode) -> None:
        self._upsert(
            receipt_dag_nodes,
            "node_id",
            value.node_id,
            value,
            project_scoped_key=True,
            kind=value.kind.value,
            node_digest=value.node_digest,
        )

    def put_edge(self, value: ReceiptDagEdge) -> None:
        self._upsert(
            receipt_dag_edges,
            "edge_id",
            value.edge_id,
            value,
            project_scoped_key=True,
            parent_node_id=value.parent_node_id,
            child_node_id=value.child_node_id,
            edge_digest=value.edge_digest,
        )

    def list_nodes(self, project_id: str) -> tuple[ReceiptDagNode, ...]:
        return self._list(receipt_dag_nodes, project_id, ReceiptDagNode)

    def list_edges(self, project_id: str) -> tuple[ReceiptDagEdge, ...]:
        return self._list(receipt_dag_edges, project_id, ReceiptDagEdge)

    def put_manifest(self, value: ReceiptDagManifest) -> None:
        self._upsert(
            receipt_dag_manifests,
            "manifest_id",
            value.manifest_id,
            value,
            manifest_digest=value.manifest_digest,
        )

    def read_manifest(self, project_id: str) -> ReceiptDagManifest | None:
        values = self._list(receipt_dag_manifests, project_id, ReceiptDagManifest)
        return None if not values else values[-1]

    def put_bundle(self, value: ReceiptBundle) -> None:
        self._upsert(
            receipt_dag_bundles,
            "bundle_id",
            value.bundle_id,
            value,
            bundle_digest=value.bundle_digest,
        )

    def read_bundle(self, project_id: str, bundle_id: str) -> ReceiptBundle | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                select(receipt_dag_bundles.c.content_json).where(
                    receipt_dag_bundles.c.project_id == project_id,
                    receipt_dag_bundles.c.bundle_id == bundle_id,
                )
            ).first()
        return (
            None
            if row is None
            else ReceiptBundle.model_validate(orjson.loads(str(row.content_json)))
        )

    def put_verification(self, value: ReceiptDagVerification) -> None:
        self._upsert(
            receipt_dag_verifications,
            "verification_id",
            value.verification_id,
            value,
            subject_id=value.subject_id,
            verification_digest=value.verification_digest,
        )

    def list_verifications(
        self, project_id: str, subject_id: str | None = None
    ) -> tuple[ReceiptDagVerification, ...]:
        statement = select(receipt_dag_verifications.c.content_json).where(
            receipt_dag_verifications.c.project_id == project_id
        )
        if subject_id is not None:
            statement = statement.where(receipt_dag_verifications.c.subject_id == subject_id)
        with self._engine.connect() as connection:
            rows = connection.execute(statement).all()
        return tuple(
            ReceiptDagVerification.model_validate(orjson.loads(str(row[0]))) for row in rows
        )

    def _upsert(
        self,
        table: Table,
        key: str,
        identifier: str,
        value: DomainModel,
        project_scoped_key: bool = False,
        **extra: object,
    ) -> None:
        project_id = str(value.model_dump(mode="python")["project_id"])
        payload: dict[str, object] = {
            key: identifier,
            "project_id": project_id,
            "content_json": _dump(value.model_dump(mode="json")),
            **extra,
        }
        statement = sqlite_insert(table).values(**payload)
        with self._engine.begin() as connection:
            if not project_scoped_key:
                existing_project = connection.execute(
                    select(table.c.project_id).where(table.c[key] == identifier)
                ).scalar_one_or_none()
                if existing_project is not None and str(existing_project) != project_id:
                    raise ValueError("receipt DAG identifier belongs to another project")
            connection.execute(
                statement.on_conflict_do_update(
                    index_elements=(["project_id", key] if project_scoped_key else [key]),
                    set_={
                        name: child
                        for name, child in payload.items()
                        if name not in {key, "project_id"}
                    },
                )
            )

    def _list(self, table: Table, project_id: str, model: type[TRecord]) -> tuple[TRecord, ...]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(table.c.content_json).where(table.c.project_id == project_id)
            ).all()
        return tuple(model.model_validate(orjson.loads(str(cast(object, row[0])))) for row in rows)
