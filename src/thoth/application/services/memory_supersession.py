"""The one rule for which stored memory versions a newer accepted version has replaced."""

from __future__ import annotations

from collections.abc import Iterable

from thoth.domain.memory import FullMemoryRevision, MemoryTransition


def superseded_memory_digests(revisions: Iterable[FullMemoryRevision]) -> frozenset[str]:
    """Digests of versions that an accepted (COMMIT) child names as its parent.

    A held, revise-needed or quarantined correction replaces nothing: the original keeps
    being used until a correction is accepted. Recall and the version list both call this.
    """

    return frozenset(
        item.parent_revision_digest
        for item in revisions
        if item.parent_revision_digest is not None and item.transition == MemoryTransition.COMMIT
    )


def latest_memory_digest(revisions: Iterable[FullMemoryRevision], digest: str) -> str:
    """Follow accepted replacements from one version to the version in use now."""

    accepted = sorted(
        (item for item in revisions if item.transition == MemoryTransition.COMMIT),
        key=lambda item: item.created_at,
    )
    child = {item.parent_revision_digest: item.revision_digest for item in accepted}
    seen = {digest}
    while digest in child and child[digest] not in seen:
        digest = child[digest]
        seen.add(digest)
    return digest
