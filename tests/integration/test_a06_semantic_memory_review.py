from __future__ import annotations

from thoth.adapters.memory.local_embedding import LocalMemoryEmbedding
from thoth.adapters.memory.model_reviewer import DeterministicRoleMemoryReviewer
from thoth.adapters.memory.relation_projection import LocalRelationProjectionBuilder
from thoth.domain.memory import MemoryReviewRole


def test_memory_roles_receive_distinct_bounded_semantic_contexts() -> None:
    reviewer = DeterministicRoleMemoryReviewer()
    contexts = {role: reviewer.context_fields(role) for role in MemoryReviewRole}
    assert len({tuple(value) for value in contexts.values()}) == 4
    assert "owner_revision_ref" in contexts[MemoryReviewRole.FACTS]
    assert "reusability" in contexts[MemoryReviewRole.REFLECTION]
    assert "alternative_explanations" in contexts[MemoryReviewRole.DREAM]
    assert "independent_verdicts" in contexts[MemoryReviewRole.TEAM]


def test_local_semantic_projections_are_deterministic_and_nonempty() -> None:
    embedding = LocalMemoryEmbedding(dimensions=32)
    first = embedding.embed("dataset version mismatch and calibration gap")
    second = embedding.embed("dataset version mismatch and calibration gap")
    assert first == second
    assert len(first) == 32
    assert any(first)
    relations = LocalRelationProjectionBuilder().build(
        (("memory:1", "revision:1", "HYPOTHESIS:one"),)
    )
    assert relations == {"revision:1": ("memory:1",), "HYPOTHESIS:one": ("memory:1",)}
