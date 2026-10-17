"""Pure record construction for the full-project memory context pack and projections (96a).

These functions only build the selection record, the context pack, the projection checkpoint and the
derived projections from values the service has already read or chosen. The id generator and clock
ports are handed in and called in the order the service called them; nothing here reads or writes.
The recall selection itself (what is eligible, narrowed, planned and omitted) stays in the service.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime

from thoth.application.services.memory_recall import Narrowing, RecallLimits, RecallPlan
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.memory import (
    FullMemoryContextPack,
    FullMemoryRevision,
    MemoryProjection,
    MemorySelectionRecord,
)
from thoth.domain.memory_expansion import MemoryExpansionOutcome
from thoth.domain.memory_terms import Word
from thoth.ports.runtime import ClockPort, IdGeneratorPort


def selection_record(
    limits: RecallLimits,
    eligible: Sequence[FullMemoryRevision],
    plan: RecallPlan,
    narrowing: Narrowing,
    excluded: Mapping[str, list[str]],
    expansion: MemoryExpansionOutcome | None,
    added: Sequence[Word],
) -> MemorySelectionRecord:
    selection = MemorySelectionRecord(
        limits=limits.as_dict(),
        eligible=tuple(item.memory_revision_id for item in eligible),
        retrieved=tuple(item.memory_revision_id for item in plan.retrieved),
        selected=tuple(item.memory_revision_id for item in plan.selected),
        context_included=tuple(item.memory_revision_id for item in plan.included),
        excluded={reason: tuple(ids) for reason, ids in sorted(excluded.items()) if ids},
        omitted_by_limit=dict(sorted(plan.omitted_by_limit.items())),
        truncated=tuple(plan.truncated),
        estimated_tokens=plan.tokens,
        common_terms=tuple(sorted(narrowing.common_terms)),
        follow_up=bool(narrowing.follow_up_markers),
        follow_up_markers=narrowing.follow_up_markers,
        matches={
            item.memory_revision_id: narrowing.matches[item.memory_revision_id]
            for item in plan.retrieved
            if item.memory_revision_id in narrowing.matches
        },
        expansion=None
        if expansion is None
        else expansion.record.model_copy(update={"added_words": tuple(word.run for word in added)}),
    )
    return selection


def context_pack(
    project_id: str,
    thread_id: str,
    query: str,
    target_use: Literal["WORKING_CONTEXT", "ACTION_CONTEXT"],
    scope: dict[str, str],
    cutoff_at: AwareDatetime,
    included: tuple[FullMemoryRevision, ...],
    excluded: Mapping[str, list[str]],
    selection: MemorySelectionRecord | None,
    limits: RecallLimits,
    id_generator: IdGeneratorPort,
    clock: ClockPort,
) -> FullMemoryContextPack:
    counts = {reason: len(ids) for reason, ids in sorted(excluded.items()) if ids}
    if selection is None:
        selection = MemorySelectionRecord(
            injection_enabled=False,
            limits=limits.as_dict(),
            excluded={reason: tuple(ids) for reason, ids in sorted(excluded.items()) if ids},
        )
    query_draft = {
        "project_id": project_id,
        "thread_id": thread_id,
        "query": query,
        "target_use": target_use,
        "scope": scope,
        "cutoff_at": cutoff_at,
        "included": tuple(item.revision_digest for item in included),
        "excluded": counts,
    }
    context = FullMemoryContextPack(
        context_pack_id=id_generator.new("memory-context"),
        project_id=project_id,
        thread_id=thread_id,
        query=query,
        target_use=target_use,
        cutoff_at=cutoff_at,
        included=included,
        excluded_reason_counts=counts,
        query_digest=domain_digest("FULL_MEMORY_QUERY", "1.0.0", canonical_payload(query_draft)),
        injected_into_thread=bool(included),
        created_at=clock.now(),
        selection=selection,
    )
    return context


def projection_checkpoint(source_digests: tuple[str, ...]) -> str:
    checkpoint = domain_digest(
        "MEMORY_PROJECTION_CHECKPOINT",
        "1.0.0",
        canonical_payload({"source_revision_digests": source_digests}),
    )
    return checkpoint


def projection_set(
    project_id: str,
    source_digests: tuple[str, ...],
    vectors: list[tuple[str, tuple[int, ...]]],
    relations: dict[str, tuple[str, ...]],
    checkpoint: str,
    now: datetime,
) -> tuple[MemoryProjection, ...]:
    projections = tuple(
        MemoryProjection(
            projection_id=f"memory-projection:{project_id}:{kind.lower()}",
            project_id=project_id,
            projection_type=kind,
            source_revision_digests=source_digests,
            payload_digest=domain_digest(
                f"MEMORY_{kind}_PROJECTION",
                "1.0.0",
                canonical_payload(
                    {
                        "source_revision_digests": source_digests,
                        "derived": True,
                        "semantic_payload": (
                            vectors if kind == "VECTOR" else relations if kind == "GRAPH" else (),
                        ),
                    }
                ),
            ),
            rebuild_checkpoint=checkpoint,
            created_at=now,
        )
        for kind in ("KEYWORD", "VECTOR", "GRAPH", "SUMMARY")
    )
    return projections
