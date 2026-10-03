"""Read-only view of every stored memory version and why it would or would not be recalled."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from pydantic import Field, JsonValue

from thoth.application.services.memory_recall import AUTO_MEMORY_NO_EVIDENCE, lacks_evidence
from thoth.application.services.memory_summary import memory_is_container, memory_summary
from thoth.application.services.memory_supersession import superseded_memory_digests
from thoth.domain.base import DomainModel
from thoth.domain.memory import FullMemoryRevision, MemoryTransition
from thoth.ports.ledger import LedgerPort
from thoth.ports.memory import FullMemoryStorePort


class RevisionListInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)


class MemoryRevisionHandlers:
    """Query-independent state only: which memory a question recalls is decided per question."""

    def __init__(
        self,
        *,
        full: FullMemoryStorePort,
        ledger: LedgerPort,
        owner_state: Callable[[str, str], str],
    ) -> None:
        self._full, self._ledger, self._owner_state = full, ledger, owner_state

    async def revision_list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = RevisionListInput.model_validate(value)
        revisions = self._full.list_revisions(request.project_id)
        heads = frozenset(self._ledger.read_heads(request.project_id).values())
        superseded = superseded_memory_digests(revisions)
        rows = [self._row(item, heads, superseded, request.project_id) for item in revisions]
        recallable = sum(1 for row in rows if row["not_recalled_because"] is None)
        return {
            "revisions": cast(list[JsonValue], rows),
            "counts": {
                "total": len(rows),
                "recallable": recallable,
                "not_recalled": len(rows) - recallable,
            },
        }

    def describe(self, project_id: str, item: FullMemoryRevision) -> dict[str, JsonValue]:
        """One version's row, read against the current heads and the current replacement rule."""

        heads = frozenset(self._ledger.read_heads(project_id).values())
        superseded = superseded_memory_digests(self._full.list_revisions(project_id))
        return self._row(item, heads, superseded, project_id)

    def _row(
        self,
        item: FullMemoryRevision,
        heads: frozenset[str],
        superseded: frozenset[str],
        project_id: str,
    ) -> dict[str, JsonValue]:
        owner_current = item.owner_revision_ref in heads
        is_latest = item.revision_digest not in superseded
        owner_state = (
            self._owner_state(project_id, item.owner_revision_ref) if owner_current else ""
        )
        return {
            "memory_id": item.memory_id,
            "memory_revision_id": item.memory_revision_id,
            "revision_digest": item.revision_digest,
            "parent_revision_digest": item.parent_revision_digest,
            "owner_revision_ref": item.owner_revision_ref,
            "origin_thread_id": item.origin_thread_id,
            "kind": item.kind.value,
            "summary": memory_summary(item, self._ledger),
            "assertion": item.assertion,
            "source_ref": item.source_ref,
            "content_excerpt": item.content_excerpt,
            "evidence_count": len(item.evidence_refs),
            "evidence_refs": list(item.evidence_refs),
            "transition": item.transition.value,
            "support_status": item.support_status,
            "authority_status": item.authority_status,
            "cutoff_valid": item.cutoff_valid,
            "recall_eligible": item.recall_eligible,
            "action_eligible": item.action_eligible,
            "owner_is_current": owner_current,
            "is_latest": is_latest,
            "not_recalled_because": _reason(
                item,
                owner_current,
                is_latest,
                owner_state,
                lambda: memory_is_container(item, self._ledger),
            ),
            "created_at": item.created_at.isoformat(),
        }


def _reason(
    item: FullMemoryRevision,
    owner_current: bool,
    is_latest: bool,
    owner_state: str,
    is_container: Callable[[], bool],
) -> str | None:
    """First failing condition, in the order the recall itself checks them."""

    if not is_latest:
        return "SUPERSEDED_BY_NEWER_VERSION"
    if item.transition != MemoryTransition.COMMIT:
        return f"TRANSITION_{item.transition.value}"
    if item.support_status != "SUPPORTED":
        return "AMBIGUOUS_OR_CONFLICTING"
    if item.authority_status != "AUTHORITATIVE":
        return "AUTHORITY_INVALID"
    if not item.cutoff_valid:
        return "CUTOFF_INVALID"
    if not owner_current:
        return "OWNER_REVISION_NOT_CURRENT"
    if owner_state != "CURRENT":
        return "DEPENDENCY_REVIEW_REQUIRED"
    if not item.recall_eligible:
        return "RECALL_INELIGIBLE"
    if is_container():
        return "CONTAINER_NOT_RECALLED"
    if lacks_evidence(item):
        return AUTO_MEMORY_NO_EVIDENCE
    return None
