from __future__ import annotations

from typing import Protocol

from thoth.domain.memory import (
    FullMemoryContextPack,
    FullMemoryRevision,
    MemoryProjection,
    MemoryRecord,
    MemoryReviewContext,
    MemoryReviewRole,
    MemoryRoleReview,
    MemoryTransitionReceipt,
)


class MemoryStorePort(Protocol):
    def add(self, record: MemoryRecord) -> None: ...

    def list(self, project_id: str | None = None) -> tuple[MemoryRecord, ...]: ...


class FullMemoryStorePort(Protocol):
    def commit_transition(
        self,
        revision: FullMemoryRevision,
        receipt: MemoryTransitionReceipt,
    ) -> None: ...

    def read_by_source_memory_id(
        self, project_id: str, memory_id: str
    ) -> FullMemoryRevision | None: ...

    def list_revisions(self, project_id: str) -> tuple[FullMemoryRevision, ...]: ...

    def list_receipts(self, project_id: str) -> tuple[MemoryTransitionReceipt, ...]: ...

    def put_context(self, context: FullMemoryContextPack) -> None: ...

    def list_contexts(self, project_id: str) -> tuple[FullMemoryContextPack, ...]: ...

    def replace_projections(
        self, project_id: str, projections: tuple[MemoryProjection, ...]
    ) -> None: ...

    def list_projections(self, project_id: str) -> tuple[MemoryProjection, ...]: ...


class MemoryEmbeddingPort(Protocol):
    def embed(self, text: str) -> tuple[int, ...]: ...


class MemoryRerankerPort(Protocol):
    def rank(self, query: str, candidates: tuple[tuple[str, str], ...]) -> tuple[str, ...]: ...


class MemoryReviewerPort(Protocol):
    def context_fields(self, role: MemoryReviewRole) -> tuple[str, ...]: ...
    async def review(self, context: MemoryReviewContext) -> MemoryRoleReview: ...


class MemoryProjectionBuilderPort(Protocol):
    def build(self, records: tuple[tuple[str, str, str], ...]) -> dict[str, tuple[str, ...]]: ...
