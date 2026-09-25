from __future__ import annotations

from collections import Counter

from thoth.domain.enums import MemoryLifecycle, RecallEligibility
from thoth.domain.memory import MemoryRecord, RecallContext

_ACTIVE_ELIGIBILITY = frozenset(
    {RecallEligibility.WORKING_CONTEXT, RecallEligibility.ACTION_CONTEXT}
)


def build_recall_context(
    *,
    project_id: str,
    records: tuple[MemoryRecord, ...],
    current_revision_refs: frozenset[str],
    character_budget: int,
    ineligible_owner_refs: frozenset[str] = frozenset(),
) -> RecallContext:
    if character_budget < 0:
        raise ValueError("character budget cannot be negative")
    excluded: Counter[str] = Counter()
    included: list[MemoryRecord] = []
    used = 0
    for record in sorted(records, key=lambda item: item.memory_id):
        if record.project_id != project_id:
            excluded["PROJECT_MISMATCH"] += 1
            continue
        if record.lifecycle != MemoryLifecycle.CURRENT:
            excluded["NOT_CURRENT"] += 1
            continue
        if record.owner_revision_ref not in current_revision_refs:
            excluded["OWNER_REVISION_NOT_CURRENT"] += 1
            continue
        if record.owner_revision_ref in ineligible_owner_refs:
            excluded["DEPENDENCY_REVIEW_REQUIRED"] += 1
            continue
        if record.recall_eligibility not in _ACTIVE_ELIGIBILITY:
            excluded["RECALL_CLASS_NOT_ACTIVE"] += 1
            continue
        size = len(record.source_ref or "") + len(record.assertion or "")
        if used + size > character_budget:
            excluded["BUDGET"] += 1
            continue
        included.append(record)
        used += size
    return RecallContext(
        project_id=project_id,
        included_records=tuple(included),
        included_memory_ids=tuple(record.memory_id for record in included),
        included_revision_refs=tuple(
            dict.fromkeys(record.owner_revision_ref for record in included)
        ),
        excluded_reason_counts=dict(sorted(excluded.items())),
        approximate_characters=used,
    )
