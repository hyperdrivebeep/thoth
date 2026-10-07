"""The input of action/create.

Moved out of actions_full as it was, with one added field: the discriminating tests an action was
drafted from. They are a field of the request now, so the specification carries only what the
action is and does; the revision digest covers the field (a record made the old way, with the refs
inside its specification, is still read by the record's own before-validator).
"""

from __future__ import annotations

from pydantic import Field, JsonValue

from thoth.domain.action_full import ActionTestRef
from thoth.domain.base import DomainModel


class CreateInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)
    object_id: str = Field(min_length=1, max_length=160)
    portfolio_id: str | None = Field(default=None, max_length=160)
    hypothesis_refs: tuple[str, ...] = ()
    primary_purpose: str = Field(min_length=1, max_length=80)
    secondary_purposes: tuple[str, ...] = ()
    specification: dict[str, JsonValue]
    evidence_refs: tuple[str, ...]
    expected_object_revision: str | None = Field(default=None, min_length=64, max_length=64)
    test_refs: tuple[ActionTestRef, ...] = ()
