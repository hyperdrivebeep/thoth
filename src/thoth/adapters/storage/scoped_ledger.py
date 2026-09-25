"""Payload access over the canonical ledger; canonical concurrency metadata stays intact."""

from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager

from sqlalchemy import select

from thoth.adapters.storage.schema import semantic_revisions, structural_nodes
from thoth.adapters.storage.sqlite import SqliteLedger
from thoth.adapters.storage.transaction import read_connection
from thoth.domain.enums import ImpactStatus
from thoth.domain.receipt import Receipt
from thoth.domain.resource_scope import ResourceScopeError
from thoth.domain.revision import EntitySnapshot, SemanticRevision
from thoth.ports.ledger import LedgerPort, LedgerTransactionPort
from thoth.ports.resource_scope import ResourceAccessPort


class ScopedLedger(LedgerPort):
    def __init__(self, raw: SqliteLedger, access: ResourceAccessPort) -> None:
        self._raw = raw
        self._access = access

    def initialize(self) -> None:
        self._raw.initialize()

    def transaction(self) -> AbstractContextManager[LedgerTransactionPort]:
        return self._raw.transaction()

    def read_heads(self, project_id: str) -> Mapping[str, str]:
        # Never use a filtered map as a canonical CAS/head-set basis.
        self._access.require_reads(project_id, ())
        return self._raw.read_heads(project_id)

    def read_revisions(
        self, project_id: str, entity_type: str, entity_id: str
    ) -> tuple[SemanticRevision, ...]:
        return tuple(
            r
            for r in self._raw.read_revisions(project_id, entity_type, entity_id)
            if self._access.may_read_revision(project_id, r.revision_digest)
        )

    def read_revision_by_digest(
        self, project_id: str, revision_digest: str
    ) -> SemanticRevision | None:
        revision = self._raw.read_revision_by_digest(project_id, revision_digest)
        if revision is not None:
            self._access.require_revision(project_id, revision_digest)
        return revision

    def read_snapshot(self, snapshot_id: str) -> EntitySnapshot | None:
        snapshot = self._raw.read_snapshot(snapshot_id)
        if snapshot is None:
            return None
        with read_connection(self._raw.engine) as connection:
            matches = connection.execute(
                select(semantic_revisions.c.project_id, semantic_revisions.c.revision_digest).where(
                    semantic_revisions.c.snapshot_id == snapshot_id
                )
            ).all()
        if not matches:
            raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")
        if any(str(row.project_id) != snapshot.project_id for row in matches):
            raise ResourceScopeError("RESOURCE_SCOPE_PROJECT_MISMATCH")
        if not any(
            self._access.may_read_revision(snapshot.project_id, str(row.revision_digest))
            for row in matches
        ):
            raise ResourceScopeError("RESOURCE_ACCESS_DENIED")
        return snapshot

    def read_revision_by_id(self, project_id: str, revision_id: str) -> SemanticRevision | None:
        revision = self._raw.read_revision_by_id(project_id, revision_id)
        if revision is not None:
            self._access.require_revision(project_id, revision.revision_digest)
        return revision

    def read_receipts(self, project_id: str) -> tuple[Receipt, ...]:
        return tuple(
            r
            for r in self._raw.read_receipts(project_id)
            if self._access.may_read(project_id, f"receipt:{r.receipt_digest}")
        )

    def read_dependency_states(self, project_id: str) -> dict[str, ImpactStatus]:
        self._access.require_reads(project_id, ())
        return self._raw.read_dependency_states(project_id)

    def search_text(self, project_id: str, query: str) -> Iterator[tuple[str, str]]:
        with read_connection(self._raw.engine) as connection:
            rows = connection.execute(
                select(structural_nodes.c.node_id, structural_nodes.c.artifact_id).where(
                    structural_nodes.c.project_id == project_id
                )
            ).all()
        allowed_artifacts = {
            str(row.artifact_id)
            for row in rows
            if self._access.may_read(project_id, str(row.artifact_id))
        }
        allowed_nodes = {
            str(row.node_id) for row in rows if str(row.artifact_id) in allowed_artifacts
        }
        yield from (
            item for item in self._raw.search_text(project_id, query) if item[0] in allowed_nodes
        )
