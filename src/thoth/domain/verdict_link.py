"""Which verdict a hypothesis came from, whether that verdict has changed, and a person's re-check.

A hypothesis made by an investigation that started from a trace row remembers the verdict it
stood on: the row, the verdict's revision and its content digest (what the verdict says, not who
confirmed it or when it was recomputed) and its state. Read against today's verdict, the link is
CURRENT, STALE (the verdict's content changed, or its row is gone) or RECHECKED (a person looked
at this very change and kept the link). A person's confirmation of a verdict is not a change, so it
never makes a link stale. A re-check is an event; nothing is overwritten.

A stale link also says how it changed. When the verdict's state, its computed reasons and the rule
that applied are the same and only the results or evidence positions behind it differ, the change
is called EVIDENCE_ONLY. That is a wording for the screen: the link is stale all the same.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, Field

from thoth.domain.base import DomainModel
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.verification_trace import TRACE_SCHEMA_VERSION, VerdictDraft

LINK_SCHEMA_VERSION = "1.0.0"
MISSING_VERDICT = "MISSING"
_MET = frozenset({"PASS_COMPUTED", "PASS"})
_FAILED = frozenset({"FAIL_COMPUTED", "FAIL", "FAIL_WITH_INCOMPLETE_COVERAGE"})

RecheckReason = Literal["UNRELATED", "STILL_MATCHES", "NEEDS_RESEARCH", "OTHER"]
RECHECK_REASONS: tuple[RecheckReason, ...] = (
    "UNRELATED",
    "STILL_MATCHES",
    "NEEDS_RESEARCH",
    "OTHER",
)
# A reason that keeps the link: the person says the hypothesis still stands on this verdict.
KEEPS_LINK: frozenset[str] = frozenset({"UNRELATED", "STILL_MATCHES", "OTHER"})


class LinkState(StrEnum):
    NONE = "NONE"
    CURRENT = "CURRENT"
    STALE = "STALE"
    RECHECKED = "RECHECKED"


class VerdictLink(DomainModel):
    """Stored on the hypothesis record; absent on a hypothesis that did not start from a row."""

    schema_version: Literal["1.0.0"] = LINK_SCHEMA_VERSION
    subject_kind: Literal["CRITERION", "REQUIREMENT"]
    subject_id: str = Field(min_length=1, max_length=500)
    verdict_revision: str = Field(min_length=64, max_length=64)
    verdict_digest: str = Field(min_length=1, max_length=200)
    state: str = Field(min_length=1, max_length=100)


class CurrentVerdict(DomainModel):
    """A row's verdict as the trace has it now."""

    verdict_revision: str
    verdict_digest: str
    state: str
    core_digest: str | None = None  # see verdict_core_digest; None when it cannot be worked out


class LinkRecheckEvent(DomainModel):
    event_id: str = Field(min_length=1)
    batch_id: str = Field(min_length=1)  # events written by one call, for one verdict change
    hypothesis_id: str = Field(min_length=1)
    hypothesis_revision_digest: str = Field(min_length=64, max_length=64)
    subject_kind: Literal["CRITERION", "REQUIREMENT"]
    subject_id: str = Field(min_length=1)
    link_verdict_digest: str  # the verdict the hypothesis stood on
    link_state: str
    current_verdict_revision: str  # the verdict the person looked at
    current_verdict_digest: str
    current_state: str
    flipped: bool
    reason_code: RecheckReason
    note: str = Field(default="", max_length=2_000)
    actor_id: str = Field(min_length=1, max_length=160)
    created_at: AwareDatetime


class HypothesisLinkRecheckRecord(DomainModel):
    """Every re-check of the project, oldest first; a new write adds events and removes none."""

    record_kind: Literal["HypothesisLinkRecheckRecord"] = "HypothesisLinkRecheckRecord"
    schema_version: Literal["2.0.0"] = "2.0.0"
    project_id: str
    events: tuple[LinkRecheckEvent, ...] = ()


LinkChange = Literal["CONTENT_CHANGED", "EVIDENCE_ONLY", "FLIPPED", "SUBJECT_MISSING"]


class LinkAssessment(DomainModel):
    state: LinkState
    change: LinkChange | None = None
    link_state: str | None = None
    current_state: str | None = None
    current_verdict_revision: str | None = None
    current_verdict_digest: str | None = None
    recheck: LinkRecheckEvent | None = None  # the event that kept (or did not keep) this change


def is_flip(before: str, after: str) -> bool:
    """A met row that now fails, or a failing row that now is met. Held rows are not a flip."""
    return (before in _MET and after in _FAILED) or (before in _FAILED and after in _MET)


def note_required(reason: str, flipped: bool) -> bool:
    """Free text is needed for "other", and to keep a link across a met/failed flip."""
    return reason == "OTHER" or (flipped and reason in KEEPS_LINK)


def verdict_core_digest(item: VerdictDraft) -> str:
    """What a verdict decided and why, without the results and evidence positions behind it.

    The same state, the same computed reasons and the same rule give the same core, whichever
    result revision or source position the verdict happened to stand on.
    """
    payload: dict[str, object] = {
        "subject_kind": item.subject_kind,
        "subject_id": item.subject_id,
        "state": item.state,
        "conditions": item.conditions,
        "computed_reasons": item.reasons.computed,
    }
    return domain_digest("TRACE_VERDICT_CORE", TRACE_SCHEMA_VERSION, canonical_payload(payload))


def assess_link(
    link: VerdictLink | None,
    current: CurrentVerdict | None,
    events: tuple[LinkRecheckEvent, ...] = (),
    *,
    hypothesis_id: str = "",
    link_core_digest: str | None = None,
) -> LinkAssessment:
    """The link against today's verdict. Only the content digest decides, never a confirmation.

    The core digests (the link's, when known, and the current one) only name the kind of change.
    """
    if link is None:
        return LinkAssessment(state=LinkState.NONE)
    change: LinkChange
    if current is None:
        change = "SUBJECT_MISSING"
        digest = MISSING_VERDICT
    else:
        if current.verdict_digest == link.verdict_digest:
            return LinkAssessment(
                state=LinkState.CURRENT,
                link_state=link.state,
                current_state=current.state,
                current_verdict_revision=current.verdict_revision,
                current_verdict_digest=current.verdict_digest,
            )
        if is_flip(link.state, current.state):
            change = "FLIPPED"
        elif link_core_digest is not None and current.core_digest == link_core_digest:
            change = "EVIDENCE_ONLY"
        else:
            change = "CONTENT_CHANGED"
        digest = current.verdict_digest
    seen = next(
        (
            event
            for event in reversed(events)
            if event.hypothesis_id == hypothesis_id
            and event.link_verdict_digest == link.verdict_digest
            and event.current_verdict_digest == digest
        ),
        None,
    )
    kept = seen is not None and seen.reason_code in KEEPS_LINK
    return LinkAssessment(
        state=LinkState.RECHECKED if kept else LinkState.STALE,
        change=change,
        link_state=link.state,
        current_state=None if current is None else current.state,
        current_verdict_revision=None if current is None else current.verdict_revision,
        current_verdict_digest=digest,
        recheck=seen,
    )
