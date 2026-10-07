"""What a person records about a trace row that was not met, and the one closure rules compute.

A person's closure is a decision made outside the system (a fix was made, the row was closed by
hand, a deviation or waiver was approved, an operating condition changed). It is recorded with the
document it rests on and never changes the verdict, which keeps saying what the rule computed.
THOTH grants no waiver. "Effect confirmed" is the only one the rules work out: after a fix was
recorded for a row that was not met, a later verdict of that row is met on results the earlier
verdict did not stand on.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import AwareDatetime, Field

from thoth.domain.base import DomainModel
from thoth.domain.verification_trace import VerdictRevision

ClosureKind = Literal["FIX_APPLIED", "HUMAN_CLOSED", "WAIVER_RECORDED", "CONDITION_CHANGED"]
CLOSURE_KINDS: tuple[ClosureKind, ...] = (
    "FIX_APPLIED",
    "HUMAN_CLOSED",
    "WAIVER_RECORDED",
    "CONDITION_CHANGED",
)
_MET = frozenset({"PASS_COMPUTED", "PASS"})


class ClosureEvent(DomainModel):
    event_id: str = Field(min_length=1)
    subject_kind: Literal["CRITERION", "REQUIREMENT"]
    subject_id: str = Field(min_length=1, max_length=500)
    kind: ClosureKind
    basis_ref: str = Field(min_length=1, max_length=500)  # the document the decision rests on
    note: str = Field(default="", max_length=2_000)
    scope: str | None = Field(default=None, max_length=500)  # for a changed operating condition
    verdict_revision: str = Field(min_length=64, max_length=64)  # the verdict the person looked at
    verdict_digest: str = Field(min_length=1, max_length=200)
    verdict_state: str = Field(min_length=1, max_length=100)  # what the rule said at that time
    actor_id: str = Field(min_length=1, max_length=160)
    created_at: AwareDatetime


class TraceClosureRecord(DomainModel):
    """Every closure of the project's trace rows, oldest first; a write adds, never removes."""

    record_kind: Literal["TraceClosureRecord"] = "TraceClosureRecord"
    schema_version: Literal["2.0.0"] = "2.0.0"
    project_id: str
    events: tuple[ClosureEvent, ...] = ()


def is_met(state: str) -> bool:
    return state in _MET


def _stands_on(item: VerdictRevision) -> tuple[str, ...]:
    """What the verdict was computed from: the chosen results, or for a requirement its criteria."""
    if item.selection is not None:
        return tuple(f"{c.result_id}@{c.result_revision}" for c in item.selection.chosen)
    return item.basis.child_verdict_digests


def effect_confirmed_by(
    before: VerdictRevision, later: Sequence[VerdictRevision]
) -> VerdictRevision | None:
    """The first later verdict that is met on new results, when the verdict at the fix was not met.

    `later` holds only verdicts computed after the fix was recorded, so a change that came before
    the fix is never counted.
    """
    if is_met(before.state.value):
        return None
    known = _stands_on(before)
    return next(
        (item for item in later if is_met(item.state.value) and _stands_on(item) != known),
        None,
    )
