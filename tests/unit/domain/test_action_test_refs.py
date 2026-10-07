"""An action record names the test it was drafted from, and older records load unchanged."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from thoth.domain.action_full import ActionRecord, ActionTestRef

REF = {"hypothesis_id": "hypothesis:a", "test_id": "test-1"}


def record(**patch: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "action_revision_id": "action-revision:1",
        "action_id": "action:1",
        "project_id": "p",
        "object_id": "object:1",
        "portfolio_id": "portfolio:1",
        "primary_purpose": "HYPOTHESIS_DISCRIMINATION",
        "specification": {"description": "compare"},
        "evidence_refs": (),
        "expected_observation_or_change": {"description": "a reading"},
        "effect_vector": {},
        "impact_set": {},
        "risk_tier": "R0",
        "required_processes": (),
        "required_roles": (),
        "revision_digest": "a" * 64,
        "created_at": datetime(2026, 10, 6, tzinfo=UTC),
    }
    return {**base, **patch}


def test_a_record_stored_before_the_field_existed_has_no_test_refs() -> None:
    assert ActionRecord.model_validate(record()).test_refs == ()


def test_the_test_a_draft_names_in_its_specification_becomes_the_field() -> None:
    found = ActionRecord.model_validate(record(specification={"test_refs": [REF]}))
    assert found.test_refs == (ActionTestRef(**REF),)


def test_a_later_revision_keeps_the_refs_it_carries_and_survives_a_round_trip() -> None:
    first = ActionRecord.model_validate(record(specification={"test_refs": [REF]}))
    # a later revision may rewrite the specification; the field stays, it is not read again
    later = ActionRecord.model_validate({**first.model_dump(mode="python"), "specification": {}})
    assert later.test_refs == first.test_refs
    assert ActionRecord.model_validate(later.model_dump(mode="json")).test_refs == first.test_refs
