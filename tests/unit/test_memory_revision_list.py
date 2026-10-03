"""memory/revision/list shows each stored memory version and why it would not be recalled."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest
from pydantic import JsonValue

from thoth.application.commands.memory_revisions import MemoryRevisionHandlers
from thoth.domain.enums import MemoryKind, MemoryPayloadMode
from thoth.domain.memory import (
    FullMemoryRevision,
    MemoryReviewRole,
    MemoryReviewVerdict,
    MemoryRoleReview,
    MemoryTransition,
)

NOW = datetime(2026, 9, 30, tzinfo=UTC)
OWNER = "a" * 64


def revision(
    tag: str,
    *,
    memory_id: str | None = None,
    owner: str = OWNER,
    transition: MemoryTransition = MemoryTransition.COMMIT,
    support: str = "SUPPORTED",
    authority: str = "AUTHORITATIVE",
    cutoff_valid: bool = True,
    recall: bool = True,
    parent: str | None = None,
    assertion: str | None = "표본이 작으면 결론을 보류한다",
) -> FullMemoryRevision:
    committed = transition == MemoryTransition.COMMIT
    return FullMemoryRevision(
        memory_revision_id=f"rev:{tag}",
        memory_id=memory_id or f"memory:{tag}",
        project_id="p",
        origin_thread_id="thread:1",
        payload_mode=MemoryPayloadMode.MEMORY_ASSERTION,
        kind=MemoryKind.LESSON,
        owner_revision_ref=owner,
        assertion=assertion,
        content_excerpt="excerpt",
        scope={},
        evidence_refs=("span:1", "span:2"),
        query_terms=("표본",),
        support_status=support,
        authority_status=authority,
        cutoff_at=NOW,
        cutoff_valid=cutoff_valid,
        reviews=tuple(
            MemoryRoleReview(
                role=role, verdict=MemoryReviewVerdict.PASS, reason_code="OK", basis_digest="b" * 64
            )
            for role in MemoryReviewRole
        ),
        transition=transition,
        recall_eligible=recall and committed,
        action_eligible=False,
        parent_revision_digest=parent,
        revision_digest=(tag * 64)[:64],
        created_at=NOW,
    )


class Full:
    def __init__(self, items: tuple[FullMemoryRevision, ...]) -> None:
        self.items = items

    def list_revisions(self, project_id: str) -> tuple[FullMemoryRevision, ...]:
        return self.items


class Ledger:
    def __init__(self, heads: dict[str, str]) -> None:
        self.heads = heads

    def read_heads(self, project_id: str) -> dict[str, str]:
        return self.heads


async def listed(
    items: tuple[FullMemoryRevision, ...],
    *,
    heads: dict[str, str] | None = None,
    owner_state: str = "CURRENT",
) -> dict[str, JsonValue]:
    handlers = MemoryRevisionHandlers(
        full=Full(items),  # type: ignore[arg-type]
        ledger=Ledger({"THREAD:x": OWNER} if heads is None else heads),  # type: ignore[arg-type]
        owner_state=lambda project_id, owner: owner_state,
    )
    return await handlers.revision_list({"project_id": "p"})


def rows(body: dict[str, JsonValue]) -> dict[str, dict[str, JsonValue]]:
    return {
        str(item["memory_revision_id"]): item
        for item in cast(list[dict[str, JsonValue]], body["revisions"])
    }


@pytest.mark.asyncio
async def test_a_recallable_memory_carries_its_query_independent_state_and_no_reason() -> None:
    body = await listed((revision("1"),))
    row = rows(body)["rev:1"]
    assert row["transition"] == "COMMIT" and row["recall_eligible"] is True
    assert row["support_status"] == "SUPPORTED" and row["authority_status"] == "AUTHORITATIVE"
    assert (
        row["cutoff_valid"] is True and row["owner_is_current"] is True and row["is_latest"] is True
    )
    assert row["not_recalled_because"] is None and row["evidence_count"] == 2
    assert row["assertion"] == "표본이 작으면 결론을 보류한다"
    assert body["counts"] == {"total": 1, "recallable": 1, "not_recalled": 0}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "heads", "owner_state", "reason"),
    [
        ({"transition": MemoryTransition.HOLD}, None, "CURRENT", "TRANSITION_HOLD"),
        ({"transition": MemoryTransition.QUARANTINE}, None, "CURRENT", "TRANSITION_QUARANTINE"),
        ({"support": "CONFLICTING"}, None, "CURRENT", "AMBIGUOUS_OR_CONFLICTING"),
        ({"authority": "UNKNOWN"}, None, "CURRENT", "AUTHORITY_INVALID"),
        ({"cutoff_valid": False}, None, "CURRENT", "CUTOFF_INVALID"),
        ({}, {"THREAD:x": "f" * 64}, "CURRENT", "OWNER_REVISION_NOT_CURRENT"),
        ({}, None, "REVIEW_REQUIRED", "DEPENDENCY_REVIEW_REQUIRED"),
        ({"recall": False}, None, "CURRENT", "RECALL_INELIGIBLE"),
    ],
)
async def test_the_first_failing_condition_is_reported_as_the_reason(
    kwargs: dict[str, object], heads: dict[str, str] | None, owner_state: str, reason: str
) -> None:
    body = await listed((revision("1", **kwargs),), heads=heads, owner_state=owner_state)  # type: ignore[arg-type]
    assert rows(body)["rev:1"]["not_recalled_because"] == reason
    assert body["counts"] == {"total": 1, "recallable": 0, "not_recalled": 1}


@pytest.mark.asyncio
async def test_an_older_version_is_marked_superseded_and_the_newer_one_is_latest() -> None:
    old = revision("1", memory_id="memory:same")
    new = revision("2", memory_id="memory:same", parent=old.revision_digest)
    body = await listed((old, new))
    by = rows(body)
    assert (
        by["rev:1"]["is_latest"] is False
        and by["rev:1"]["not_recalled_because"] == "SUPERSEDED_BY_NEWER_VERSION"
    )
    assert (
        by["rev:2"]["is_latest"] is True
        and by["rev:2"]["parent_revision_digest"] == old.revision_digest
    )
    assert body["counts"] == {"total": 2, "recallable": 1, "not_recalled": 1}


@pytest.mark.asyncio
async def test_an_empty_project_reads_as_an_empty_list_and_the_call_writes_nothing() -> None:
    body = await listed(())
    assert body["revisions"] == [] and body["counts"] == {
        "total": 0,
        "recallable": 0,
        "not_recalled": 0,
    }
