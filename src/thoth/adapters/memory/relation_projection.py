from __future__ import annotations

from thoth.ports.memory import MemoryProjectionBuilderPort


class LocalRelationProjectionBuilder(MemoryProjectionBuilderPort):
    def build(
        self, records: tuple[tuple[str, str, str], ...]
    ) -> dict[str, tuple[str, ...]]:
        values: dict[str, list[str]] = {}
        for memory_id, revision_ref, source_ref in records:
            values.setdefault(revision_ref, []).append(memory_id)
            values.setdefault(source_ref, []).append(memory_id)
        return {key: tuple(sorted(items)) for key, items in sorted(values.items())}
