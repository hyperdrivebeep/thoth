"""How a new memory relates to a stored one, decided from ids, lineage and fields only.

No word overlap is used. Two memories that merely share words are unrelated; a relation the
rules cannot settle is AMBIGUOUS and is held or put to a model, never guessed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from thoth.application.services.memory_shape import CONTAINER_SHAPES, RecordShape
from thoth.domain.enums import MemoryKind
from thoth.domain.memory_relation import MemoryRelation


@dataclass(frozen=True)
class MemorySubject:
    """The facts about one memory the relation rules read."""

    memory_id: str
    kind: MemoryKind
    owner_revision_ref: str
    source_ref: str | None
    scope: Mapping[str, str]
    shape: RecordShape = RecordShape.OTHER
    member_keys: frozenset[str] = frozenset()
    fields: Mapping[str, str] = field(default_factory=lambda: dict[str, str]())
    # The record versions this memory's record descends from, itself included.
    owner_lineage: frozenset[str] = frozenset()
    assertion: str | None = None
    # For a user correction: the stored version the chain of corrections started from.
    correction_root: str | None = None

    @property
    def is_container(self) -> bool:
        return bool(self.member_keys) or self.shape in CONTAINER_SHAPES


def _scope_applies(new: Mapping[str, str], existing: Mapping[str, str]) -> bool:
    # Origin workstream is provenance; scientific applicability stays scoped.
    return all(new.get(key) == value for key, value in existing.items() if key != "workstream")


def _same_text(left: str | None, right: str | None) -> bool:
    return (
        left is not None
        and right is not None
        and " ".join(left.split()).casefold() == " ".join(right.split()).casefold()
    )


def classify_memory_relation(new: MemorySubject, existing: MemorySubject) -> MemoryRelation:
    if new.kind != existing.kind or not _scope_applies(new.scope, existing.scope):
        return MemoryRelation.UNRELATED
    if new.memory_id == existing.memory_id or (
        new.source_ref is not None
        and new.source_ref == existing.source_ref
        and new.owner_revision_ref == existing.owner_revision_ref
    ):
        return MemoryRelation.DUPLICATE
    if existing.is_container or new.is_container:
        container, member = (existing, new) if existing.is_container else (new, existing)
        held = member.source_ref is not None and member.source_ref in container.member_keys
        return MemoryRelation.CONTAINS if held else MemoryRelation.UNRELATED
    if _same_text(new.assertion, existing.assertion):
        return MemoryRelation.DUPLICATE
    if new.correction_root is not None and new.correction_root == existing.correction_root:
        # Two accepted corrections of one starting version: which one stands is not decidable here.
        return MemoryRelation.AMBIGUOUS
    if new.source_ref is None or new.source_ref != existing.source_ref:
        return MemoryRelation.UNRELATED
    if existing.owner_revision_ref in new.owner_lineage or (
        new.owner_revision_ref in existing.owner_lineage
    ):
        return MemoryRelation.UPDATE
    shared = set(new.fields) & set(existing.fields)
    if not shared:
        return MemoryRelation.AMBIGUOUS
    if all(new.fields[name] == existing.fields[name] for name in shared):
        return MemoryRelation.DUPLICATE
    return MemoryRelation.CONTRADICTION_CANDIDATE
