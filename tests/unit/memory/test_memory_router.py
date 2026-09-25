from __future__ import annotations

import pytest
from pydantic import ValidationError

from thoth.application.services import build_recall_context
from thoth.domain.enums import (
    MemoryKind,
    MemoryLifecycle,
    MemoryPayloadMode,
    RecallEligibility,
)
from thoth.domain.memory import MemoryRecord


def _record(
    identifier: str,
    *,
    project_id: str = "project:1",
    owner: str = "revision:current",
    eligibility: RecallEligibility = RecallEligibility.WORKING_CONTEXT,
    lifecycle: MemoryLifecycle = MemoryLifecycle.CURRENT,
) -> MemoryRecord:
    return MemoryRecord(
        memory_id=identifier,
        project_id=project_id,
        payload_mode=MemoryPayloadMode.DOMAIN_REFERENCE,
        kind=MemoryKind.FACT,
        owner_revision_ref=owner,
        source_ref=f"span:{identifier}",
        recall_eligibility=eligibility,
        lifecycle=lifecycle,
        revision_digest="a" * 64,
    )


def test_recall_only_includes_current_same_project_active_memory() -> None:
    records = (
        _record("memory:included"),
        _record("memory:other-project", project_id="project:2"),
        _record("memory:old-owner", owner="revision:old"),
        _record("memory:audit", eligibility=RecallEligibility.AUDIT_ONLY),
        _record("memory:superseded", lifecycle=MemoryLifecycle.SUPERSEDED),
    )

    context = build_recall_context(
        project_id="project:1",
        records=records,
        current_revision_refs=frozenset({"revision:current"}),
        character_budget=10_000,
    )

    assert context.included_memory_ids == ("memory:included",)
    assert context.included_revision_refs == ("revision:current",)
    assert context.excluded_reason_counts == {
        "NOT_CURRENT": 1,
        "OWNER_REVISION_NOT_CURRENT": 1,
        "PROJECT_MISMATCH": 1,
        "RECALL_CLASS_NOT_ACTIVE": 1,
    }


def test_memory_assertion_is_limited_to_lessons() -> None:
    with pytest.raises(ValidationError, match="limited to an explicit lesson"):
        MemoryRecord(
            memory_id="memory:bad",
            project_id="project:1",
            payload_mode=MemoryPayloadMode.MEMORY_ASSERTION,
            kind=MemoryKind.FACT,
            owner_revision_ref="revision:1",
            assertion="this fact bypasses evidence",
            recall_eligibility=RecallEligibility.WORKING_CONTEXT,
            revision_digest="a" * 64,
        )

    lesson = MemoryRecord(
        memory_id="memory:lesson",
        project_id="project:1",
        payload_mode=MemoryPayloadMode.MEMORY_ASSERTION,
        kind=MemoryKind.LESSON,
        owner_revision_ref="revision:1",
        assertion="compare run IDs before comparing metrics",
        recall_eligibility=RecallEligibility.WORKING_CONTEXT,
        revision_digest="b" * 64,
    )
    assert lesson.assertion is not None
