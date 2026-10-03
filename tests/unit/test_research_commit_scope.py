"""Which heads a research save compares: what it read and wrote, or everything without a record."""

from __future__ import annotations

from dataclasses import dataclass, field

from thoth.application.services.research_commit_scope import read_scope
from thoth.domain.research_execution import ResearchWork
from thoth.domain.research_reference import RevisionRef


@dataclass
class FakeWork:
    request_ref: RevisionRef
    consumed_heads: dict[str, str] = field(default_factory=dict)


def work(**consumed: str) -> ResearchWork:
    ref = RevisionRef(
        project_id="p",
        entity_type="THREAD",
        entity_id="request:t",
        revision_id="revision:r",
        revision_digest="a" * 64,
        schema_version="1.0.0",
    )
    return FakeWork(ref, dict(consumed))  # type: ignore[return-value]


def test_without_a_research_attempt_or_for_another_project_every_head_is_compared() -> None:
    assert read_scope(None, "p", {"A:1": "x"}, ("A:1",)) is None
    assert read_scope(work(), "other", {"A:1": "x"}, ("A:1",)) is None


def test_the_scope_is_what_was_read_written_and_the_request_at_the_values_seen_at_the_start() -> (
    None
):
    heads = {"HYPOTHESIS:h": "1", "ACTION:a": "2", "THREAD:request:t": "3", "MEMORY:other": "4"}
    scope = read_scope(
        work(**{"HYPOTHESIS:h": "old", "GONE:x": "9"}), "p", heads, ("ACTION:a", "OUTCOME:new")
    )
    assert scope is not None
    assert scope.expected_heads == {"HYPOTHESIS:h": "1", "ACTION:a": "2", "THREAD:request:t": "3"}
    assert scope.expected_absent == ("OUTCOME:new",)
    assert "MEMORY:other" not in scope.expected_heads
