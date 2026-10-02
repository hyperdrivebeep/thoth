"""One readable line for a stored memory, taken from the record it points at."""

from __future__ import annotations

from collections.abc import Mapping

from thoth.application.services.memory_safety import REDACTED_MEMORY
from thoth.application.services.memory_shape import first_line, is_container, summarize_record
from thoth.domain.memory import FullMemoryRevision
from thoth.ports.ledger import LedgerPort

MAX_SUMMARY = 200
UNREADABLE = "내용을 읽을 수 없는 기억"

__all__ = ["MAX_SUMMARY", "UNREADABLE", "memory_is_container", "memory_summary", "summarize_record"]


def _owner_content(revision: FullMemoryRevision, ledger: LedgerPort) -> Mapping[str, object] | None:
    owner = ledger.read_revision_by_digest(revision.project_id, revision.owner_revision_ref)
    snapshot = None if owner is None else ledger.read_snapshot(owner.snapshot_id)
    return None if snapshot is None else snapshot.content


def memory_is_container(revision: FullMemoryRevision, ledger: LedgerPort) -> bool:
    """True when the memory stands for a portfolio or plan that groups other records.

    Read from the record it points at, so a bundle stored before bundles were left out is still
    recognised. A free-text memory is never a container.
    """

    if revision.assertion is not None:
        return False
    content = _owner_content(revision, ledger)
    return content is not None and is_container(content)


def memory_summary(revision: FullMemoryRevision, ledger: LedgerPort) -> str:
    if revision.assertion is not None:
        if revision.assertion == REDACTED_MEMORY:
            return "격리된 정정 (내용 가림)"
        return first_line(revision.assertion)
    content = _owner_content(revision, ledger)
    found = None if content is None else summarize_record(content)
    return found or UNREADABLE
