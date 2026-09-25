"""Apply current source lineage to memory reads without rewriting immutable memory."""

from __future__ import annotations

from collections.abc import Mapping

from thoth.domain.memory import (
    FullMemoryContextPack,
    FullMemoryRevision,
    MemoryProjection,
    MemoryRecord,
    MemoryTransitionReceipt,
)
from thoth.domain.resource_scope import ResourceScopeError, current_resource_uses
from thoth.domain.revision import StagedRevision
from thoth.ports.memory import FullMemoryStorePort, MemoryStorePort
from thoth.ports.resource_scope import ResourceAccessPort


class ScopedMemoryCandidates:
    def __init__(self, raw: MemoryStorePort, resources: ResourceAccessPort) -> None:
        self._raw = raw
        self._resources = resources

    def add(self, record: MemoryRecord) -> None:
        self._resources.require_revision(record.project_id, record.owner_revision_ref)
        self._raw.add(record)

    def list(self, project_id: str | None = None) -> tuple[MemoryRecord, ...]:
        return tuple(
            r
            for r in self._raw.list(project_id)
            if self._resources.may_read_revision(r.project_id, r.owner_revision_ref)
        )


class MemoryResourceAccess:
    def __init__(self, store: FullMemoryStorePort, resources: ResourceAccessPort) -> None:
        self._store = store
        self.resources = resources

    def require_current_uses(self, project_id: str) -> None:
        self.resources.require_reads(
            project_id,
            tuple(
                use.resource_ref
                for use in current_resource_uses() or ()
                if use.project_id == project_id and use.capability == "READ"
            ),
        )

    def require_owner(
        self,
        project_id: str,
        digest: str,
        staged: Mapping[str, StagedRevision],
        ancestors: frozenset[str] = frozenset(),
    ) -> None:
        if digest in ancestors or len(ancestors) >= 128:
            raise ResourceScopeError("RESOURCE_SCOPE_LINEAGE_INVALID")
        owner = staged.get(digest)
        if owner is None:
            self.resources.require_revision(project_id, digest)
            return
        revision = owner.revision
        if revision.project_id != project_id:
            raise ResourceScopeError("RESOURCE_ACCESS_DENIED")
        # Fresh staged owners seal the operation's actual reads on commit. Empty new
        # drafts stay PENDING_SOURCES; this path never relaxes an unknown legacy owner.
        self.require_current_uses(project_id)
        for reference in revision.evidence_refs:
            self.resources.require_read(project_id, reference)
        for parent in revision.parent_revision_digests:
            self.require_owner(project_id, parent, staged, ancestors | {digest})

    def require(
        self, revision: FullMemoryRevision, staged: Mapping[str, StagedRevision] | None = None
    ) -> None:
        revisions = {r.revision_digest: r for r in self._store.list_revisions(revision.project_id)}
        current = revision
        seen: set[str] = set()
        while True:
            if current.project_id != revision.project_id or current.revision_digest in seen:
                raise ResourceScopeError("RESOURCE_SCOPE_LINEAGE_INVALID")
            seen.add(current.revision_digest)
            if len(seen) > 128:
                raise ResourceScopeError("RESOURCE_SCOPE_LINEAGE_INVALID")
            if staged is None:
                self.resources.require_revision(current.project_id, current.owner_revision_ref)
            else:
                self.require_owner(current.project_id, current.owner_revision_ref, staged)
            for ref in current.evidence_refs:
                self.resources.require_read(current.project_id, ref)
            if current.parent_revision_digest is None:
                return
            parent = revisions.get(current.parent_revision_digest)
            if parent is None:
                raise ResourceScopeError("RESOURCE_LINEAGE_UNKNOWN")
            current = parent

    def may_read(self, revision: FullMemoryRevision) -> bool:
        try:
            self.require(revision)
        except ResourceScopeError as exc:
            if exc.code in {
                "RESOURCE_ACCESS_DENIED",
                "RESOURCE_SCOPE_UNKNOWN",
                "RESOURCE_REFERENCE_UNRESOLVED",
                "RESOURCE_LINEAGE_UNKNOWN",
            }:
                return False
            raise
        return True

    def can_project(
        self,
        project_id: str,
        revisions: tuple[FullMemoryRevision, ...],
        staged: tuple[StagedRevision, ...],
    ) -> bool:
        by_digest = {r.revision.revision_digest: r for r in staged}
        for revision in revisions:
            if revision.project_id != project_id:
                raise ResourceScopeError("RESOURCE_SCOPE_PROJECT_MISMATCH")
            try:
                self.require(revision, by_digest)
            except ResourceScopeError as exc:
                if exc.code in {
                    "RESOURCE_ACCESS_DENIED",
                    "RESOURCE_SCOPE_UNKNOWN",
                    "RESOURCE_REFERENCE_UNRESOLVED",
                    "RESOURCE_LINEAGE_UNKNOWN",
                }:
                    return False
                raise
        return True


class ScopedFullMemoryStore:
    """Public query view; the admission service retains the unfiltered atomic owner."""

    def __init__(self, raw: FullMemoryStorePort, access: MemoryResourceAccess) -> None:
        self._raw = raw
        self._access = access

    def commit_transition(
        self, revision: FullMemoryRevision, receipt: MemoryTransitionReceipt
    ) -> None:
        self._access.require(revision)
        self._raw.commit_transition(revision, receipt)

    def read_by_source_memory_id(
        self, project_id: str, memory_id: str
    ) -> FullMemoryRevision | None:
        item = self._raw.read_by_source_memory_id(project_id, memory_id)
        if item is not None:
            self._access.require(item)
        return item

    def list_revisions(self, project_id: str) -> tuple[FullMemoryRevision, ...]:
        return tuple(r for r in self._raw.list_revisions(project_id) if self._access.may_read(r))

    def list_receipts(self, project_id: str) -> tuple[MemoryTransitionReceipt, ...]:
        visible = {r.memory_revision_id for r in self.list_revisions(project_id)}
        return tuple(
            r for r in self._raw.list_receipts(project_id) if r.memory_revision_id in visible
        )

    def put_context(self, context: FullMemoryContextPack) -> None:
        for revision in context.included:
            if revision.project_id != context.project_id:
                raise ResourceScopeError("RESOURCE_SCOPE_LINEAGE_INVALID")
            self._access.require(revision)
        self._raw.put_context(context)

    def list_contexts(self, project_id: str) -> tuple[FullMemoryContextPack, ...]:
        # Never return a partially redacted object under its original immutable digest.
        return tuple(
            c
            for c in self._raw.list_contexts(project_id)
            if c.included
            and all(r.project_id == project_id and self._access.may_read(r) for r in c.included)
        )

    def replace_projections(
        self, project_id: str, projections: tuple[MemoryProjection, ...]
    ) -> None:
        visible = {r.revision_digest for r in self.list_revisions(project_id)}
        if any(not set(p.source_revision_digests).issubset(visible) for p in projections):
            raise ResourceScopeError("RESOURCE_ACCESS_DENIED")
        self._raw.replace_projections(project_id, projections)

    def list_projections(self, project_id: str) -> tuple[MemoryProjection, ...]:
        visible = {r.revision_digest for r in self.list_revisions(project_id)}
        return tuple(
            p
            for p in self._raw.list_projections(project_id)
            if p.source_revision_digests and set(p.source_revision_digests).issubset(visible)
        )
