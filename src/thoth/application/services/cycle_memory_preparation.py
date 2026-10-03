"""Memory review for a research save, redone once when memory changed before the save."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import suppress

from thoth.application.services.full_project_memory import FullProjectMemoryService
from thoth.application.services.memory_relation_resolver import MemoryRelationBudget
from thoth.application.services.research_commit_scope import ReadScope
from thoth.domain.memory import MemoryRecord
from thoth.domain.memory_preparation import (
    MemoryPreparationBasis,
    MemoryPreparationHeadChanged,
    PreparedMemoryPromotion,
)
from thoth.domain.revision import StagedRevision


async def prepare_cycle_memory(
    memory: FullProjectMemoryService | None,
    *,
    thread_id: str,
    basis: MemoryPreparationBasis | None,
    scope: ReadScope | None,
    expected_heads: Mapping[str, str],
    candidates: tuple[MemoryRecord, ...],
    staged: tuple[StagedRevision, ...],
    attempt: int,
    relations: MemoryRelationBudget | None = None,
) -> PreparedMemoryPromotion | None:
    """Review the candidates against the memory of this moment, outside the save.

    None when there is no memory service, or when a head the attempt read has changed (the save
    then branches as before). `attempt` above 1 marks a review that had to be redone.
    `relations` is the investigation's remaining model relation calls, shared by both attempts.
    """

    if memory is None or basis is None:
        return None
    if scope is not None:
        basis = memory.rebase_basis(
            basis, head_scope=expected_heads, head_absent=scope.expected_absent
        )
    # Preserve semantic candidates as a branch; admit no stale memory.
    with suppress(MemoryPreparationHeadChanged):
        prepared = await memory.prepare_thread_results(
            basis=basis,
            thread_id=thread_id,
            candidates=candidates,
            staged_revisions=staged,
            relation_budget=relations,
        )
        if attempt > 1:
            result = prepared.result.model_copy(update={"preparation_attempts": attempt})
            return prepared.model_copy(update={"result": result})
        return prepared
    return None
