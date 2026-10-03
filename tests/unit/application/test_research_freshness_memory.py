"""A result that used a memory version is flagged once that memory is corrected."""

from datetime import UTC, datetime
from typing import Any, cast

from thoth.application.services.research_freshness import ResearchFreshnessService
from thoth.domain.memory import FullMemoryRevision, MemoryTransition
from thoth.domain.research_basis import ResearchResultBasis
from thoth.domain.research_reference import RevisionRef

PROJECT = "project-1"
REQUEST_DIGEST = "a" * 64
OLD, NEW, OTHER = "1" * 64, "2" * 64, "3" * 64


class FakeLedger:
    def read_heads(self, project_id: str) -> dict[str, str]:
        return {"THREAD:request:t1": REQUEST_DIGEST}

    def read_dependency_states(self, project_id: str) -> dict[str, object]:
        return {}


class FakeMemory:
    def __init__(self, *revisions: FullMemoryRevision) -> None:
        self.revisions = revisions

    def list_revisions(self, project_id: str) -> tuple[FullMemoryRevision, ...]:
        return self.revisions


def _revision(digest: str, parent: str | None, transition: MemoryTransition) -> FullMemoryRevision:
    return FullMemoryRevision.model_construct(
        revision_digest=digest, parent_revision_digest=parent, transition=transition
    )


def _basis(*memory_refs: str) -> ResearchResultBasis:
    return ResearchResultBasis(
        request_ref=RevisionRef(
            project_id=PROJECT,
            entity_type="THREAD",
            entity_id="request:t1",
            revision_id="r1",
            revision_digest=REQUEST_DIGEST,
        ),
        memory_revision_refs=memory_refs,
        policy_digest="b" * 64,
        cutoff_at=datetime(2026, 9, 1, tzinfo=UTC),
        coverage="COMPLETE",
    )


def _service(memory: FakeMemory | None) -> ResearchFreshnessService:
    return ResearchFreshnessService(cast(Any, FakeLedger()), memory=cast(Any, memory))


def test_accepted_correction_of_a_used_memory_requires_review() -> None:
    memory = FakeMemory(
        _revision(OLD, None, MemoryTransition.COMMIT),
        _revision(NEW, OLD, MemoryTransition.COMMIT),
    )
    currentness = _service(memory).evaluate_result(PROJECT, _basis(OLD))
    assert currentness.state == "REVIEW_REQUIRED"
    assert currentness.reasons == ("MEMORY_CORRECTED_AFTER_RESULT",)
    assert currentness.execution_eligible is False


def test_correction_that_was_not_accepted_leaves_the_result_current() -> None:
    for transition in (
        MemoryTransition.HOLD,
        MemoryTransition.REVISE,
        MemoryTransition.QUARANTINE,
    ):
        memory = FakeMemory(
            _revision(OLD, None, MemoryTransition.COMMIT), _revision(NEW, OLD, transition)
        )
        assert _service(memory).evaluate_result(PROJECT, _basis(OLD)).state == "CURRENT"


def test_correction_of_a_memory_the_result_did_not_use_has_no_effect() -> None:
    memory = FakeMemory(
        _revision(OTHER, None, MemoryTransition.COMMIT),
        _revision(NEW, OTHER, MemoryTransition.COMMIT),
    )
    assert _service(memory).evaluate_result(PROJECT, _basis(OLD)).state == "CURRENT"


def test_result_without_memory_refs_is_unaffected() -> None:
    memory = FakeMemory(
        _revision(OLD, None, MemoryTransition.COMMIT),
        _revision(NEW, OLD, MemoryTransition.COMMIT),
    )
    assert _service(memory).evaluate_result(PROJECT, _basis()).state == "CURRENT"


def test_without_a_memory_store_behavior_is_unchanged() -> None:
    assert _service(None).evaluate_result(PROJECT, _basis(OLD)).state == "CURRENT"


def test_memory_reason_is_combined_with_other_review_reasons() -> None:
    memory = FakeMemory(
        _revision(OLD, None, MemoryTransition.COMMIT),
        _revision(NEW, OLD, MemoryTransition.COMMIT),
    )
    basis = _basis(OLD).model_copy(update={"coverage": "PARTIAL"})
    currentness = _service(memory).evaluate_result(PROJECT, basis)
    assert currentness.state == "UNKNOWN_BASIS"
    assert "MEMORY_CORRECTED_AFTER_RESULT" in currentness.reasons
