from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager
from typing import Protocol

from thoth.domain.enums import ImpactStatus
from thoth.domain.ids import ProjectId, Sha256
from thoth.domain.receipt import Receipt
from thoth.domain.revision import EntitySnapshot, ImpactPropagationPlan, SemanticRevision


class LedgerProjectionPort(Protocol):
    """Derived writes run inside the same transaction as their canonical revision."""

    def stage_revision(self, revision: SemanticRevision) -> None: ...


class ReceiptProjectionPort(Protocol):
    """Derived receipt writes share the canonical receipt transaction."""

    def stage_receipt(self, receipt: Receipt) -> None: ...


class LedgerTransactionPort(Protocol):
    def insert_snapshot(self, snapshot: EntitySnapshot) -> None: ...

    def insert_revision(self, revision: SemanticRevision) -> None: ...

    def get_head(self, project_id: ProjectId, aggregate_key: str) -> Sha256 | None: ...

    def get_heads(self, project_id: ProjectId) -> Mapping[str, Sha256]: ...

    def set_head(
        self, project_id: ProjectId, aggregate_key: str, revision_digest: Sha256
    ) -> None: ...

    def insert_receipt(self, receipt: Receipt) -> None: ...

    def apply_impact_plan(
        self,
        project_id: ProjectId,
        plan: ImpactPropagationPlan,
        *,
        caused_by_revision: Sha256,
        updated_at: str,
    ) -> None: ...


class LedgerPort(Protocol):
    def initialize(self) -> None: ...

    def transaction(self) -> AbstractContextManager[LedgerTransactionPort]: ...

    def read_heads(self, project_id: ProjectId) -> Mapping[str, Sha256]: ...

    def read_revisions(
        self, project_id: ProjectId, entity_type: str, entity_id: str
    ) -> tuple[SemanticRevision, ...]: ...

    def read_revision_by_digest(
        self, project_id: ProjectId, revision_digest: Sha256
    ) -> SemanticRevision | None: ...

    def read_revision_by_id(
        self, project_id: ProjectId, revision_id: str
    ) -> SemanticRevision | None: ...

    def read_receipts(self, project_id: ProjectId) -> tuple[Receipt, ...]: ...

    def read_snapshot(self, snapshot_id: str) -> EntitySnapshot | None: ...

    def read_dependency_states(self, project_id: ProjectId) -> dict[str, ImpactStatus]: ...

    def search_text(self, project_id: ProjectId, query: str) -> Iterator[tuple[str, str]]: ...


class ManagedLedgerPort(LedgerPort, Protocol):
    def register_projection(self, name: str, projection: LedgerProjectionPort) -> None: ...

    def register_receipt_projection(self, name: str, projection: ReceiptProjectionPort) -> None: ...

    def close(self) -> None: ...
