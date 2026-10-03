"""One rule says which memory versions a newer accepted version has replaced."""

from __future__ import annotations

import pytest
from tests.unit.test_memory_revision_list import listed, revision, rows

from thoth.application.services.memory_supersession import superseded_memory_digests
from thoth.domain.memory import MemoryTransition


def test_only_an_accepted_child_replaces_its_parent() -> None:
    old = revision("1")
    accepted = revision("2", parent=old.revision_digest)
    assert superseded_memory_digests((old, accepted)) == {old.revision_digest}
    for transition in (
        MemoryTransition.HOLD,
        MemoryTransition.REVISE,
        MemoryTransition.QUARANTINE,
    ):
        child = revision("3", parent=old.revision_digest, transition=transition)
        assert superseded_memory_digests((old, child)) == frozenset()
    assert superseded_memory_digests((old,)) == frozenset()


def test_a_chain_replaces_every_version_except_the_last() -> None:
    first = revision("1")
    second = revision("2", parent=first.revision_digest)
    third = revision("3", parent=second.revision_digest)
    assert superseded_memory_digests((first, second, third)) == {
        first.revision_digest,
        second.revision_digest,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "transition", [MemoryTransition.HOLD, MemoryTransition.REVISE, MemoryTransition.QUARANTINE]
)
async def test_the_list_keeps_a_version_current_when_its_correction_was_not_accepted(
    transition: MemoryTransition,
) -> None:
    old = revision("1")
    rejected = revision("2", parent=old.revision_digest, transition=transition)
    by = rows(await listed((old, rejected)))
    assert by["rev:1"]["is_latest"] is True and by["rev:1"]["not_recalled_because"] is None
    assert by["rev:2"]["not_recalled_because"] == f"TRANSITION_{transition.value}"
