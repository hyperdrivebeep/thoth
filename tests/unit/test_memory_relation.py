"""The relation of two memories comes from ids, lineage and fields; shared words decide nothing."""

from thoth.application.services.memory_relation import MemorySubject, classify_memory_relation
from thoth.application.services.memory_shape import RecordShape
from thoth.domain.enums import MemoryKind
from thoth.domain.memory_relation import MemoryRelation

R = MemoryRelation


def _subject(memory_id: str = "m-new", **change: object) -> MemorySubject:
    base: dict[str, object] = {
        "memory_id": memory_id,
        "kind": MemoryKind.HYPOTHESIS,
        "owner_revision_ref": f"owner-{memory_id}",
        "source_ref": f"HYPOTHESIS:hypothesis:o:{memory_id}",
        "scope": {},
        "shape": RecordShape.HYPOTHESIS,
        "fields": {"statement": "표본이 작으면 결론을 보류한다"},
    }
    return MemorySubject(**{**base, **change})  # type: ignore[arg-type]


def test_other_kind_or_other_scope_is_unrelated() -> None:
    assert (
        classify_memory_relation(_subject(), _subject("m-old", kind=MemoryKind.ACTION))
        == R.UNRELATED
    )
    left, right = _subject(scope={"field": "a"}), _subject("m-old", scope={"field": "b"})
    assert classify_memory_relation(left, right) == R.UNRELATED
    # a different workstream is only where it came from
    same = "HYPOTHESIS:h:1"
    one = _subject(scope={"workstream": "one"}, source_ref=same)
    two = _subject("m-old", scope={"workstream": "two"}, source_ref=same)
    assert classify_memory_relation(one, two) == R.DUPLICATE


def test_the_same_memory_or_the_same_record_version_is_a_duplicate() -> None:
    assert classify_memory_relation(_subject(), _subject()) == R.DUPLICATE
    same_version = _subject(
        "m-old", owner_revision_ref="owner-m-new", source_ref=_subject().source_ref
    )
    assert classify_memory_relation(_subject(), same_version) == R.DUPLICATE


def test_a_bundle_contains_its_members_and_is_unrelated_to_anyone_else() -> None:
    bundle = _subject(
        "m-bundle",
        shape=RecordShape.HYPOTHESIS_PORTFOLIO,
        source_ref="HYPOTHESIS:portfolio:o:1",
        member_keys=frozenset({"HYPOTHESIS:hypothesis:o:m-new"}),
        fields={},
    )
    assert classify_memory_relation(_subject(), bundle) == R.CONTAINS
    assert classify_memory_relation(bundle, _subject()) == R.CONTAINS
    assert classify_memory_relation(_subject("m-other"), bundle) == R.UNRELATED


def test_a_newer_version_of_the_same_record_is_an_update_not_a_conflict() -> None:
    newer = _subject(
        "m-new",
        source_ref="HYPOTHESIS:h:1",
        owner_lineage=frozenset({"owner-m-new", "owner-m-old"}),
        fields={"statement": "표본이 크면 결론을 확정한다"},
    )
    older = _subject("m-old", source_ref="HYPOTHESIS:h:1")
    assert classify_memory_relation(newer, older) == R.UPDATE
    assert classify_memory_relation(older, newer) == R.UPDATE


def test_unrelated_update_with_different_value_is_a_contradiction_candidate() -> None:
    other = _subject("m-new", source_ref="HYPOTHESIS:h:1", fields={"statement": "다른 진술"})
    existing = _subject("m-old", source_ref="HYPOTHESIS:h:1")
    assert classify_memory_relation(other, existing) == R.CONTRADICTION_CANDIDATE
    same = _subject("m-new", source_ref="HYPOTHESIS:h:1")
    assert classify_memory_relation(same, existing) == R.DUPLICATE
    unreadable = _subject("m-new", source_ref="HYPOTHESIS:h:1", fields={})
    assert classify_memory_relation(unreadable, existing) == R.AMBIGUOUS


def test_different_records_are_unrelated_even_when_they_share_words() -> None:
    one = _subject("m-one", fields={"statement": "표본이 작으면 결론을 보류한다"})
    two = _subject("m-two", fields={"statement": "표본이 작으면 결론을 확정하지 않는다"})
    assert classify_memory_relation(one, two) == R.UNRELATED


def test_two_corrections_of_one_starting_version_are_ambiguous() -> None:
    def correction(memory_id: str, text: str, root: str | None) -> MemorySubject:
        return _subject(
            memory_id,
            kind=MemoryKind.LESSON,
            source_ref=f"MEMORY:{memory_id}",
            shape=RecordShape.OTHER,
            fields={},
            assertion=text,
            correction_root=root,
        )

    first = correction("edit-a", "표본이 작으면 결론을 보류한다", "start")
    assert (
        classify_memory_relation(correction("edit-b", "표본이 크면 확정한다", "start"), first)
        == R.AMBIGUOUS
    )
    assert (
        classify_memory_relation(correction("edit-c", "표본이 크면 확정한다", "other"), first)
        == R.UNRELATED
    )
    same_words = correction("edit-d", " 표본이  작으면 결론을 보류한다", "start")
    assert classify_memory_relation(same_words, first) == R.DUPLICATE
