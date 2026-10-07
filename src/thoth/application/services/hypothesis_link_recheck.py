"""A person re-checks hypotheses whose verdict changed: an event each, nothing overwritten."""

from __future__ import annotations

from datetime import datetime

from thoth.application.services.hypothesis_link_view import (
    RECHECK_KEY,
    HypothesisLinkReader,
    LinkItem,
)
from thoth.application.services.request_records import RequestRecords
from thoth.domain.enums import EntityType
from thoth.domain.verdict_link import (
    HypothesisLinkRecheckRecord,
    LinkRecheckEvent,
    LinkState,
    RecheckReason,
    VerdictLink,
    note_required,
)

NOT_HUMAN_PREFIXES = ("system:", "agent:", "model:", "bot:")


class RecheckRefused(ValueError):
    """The re-check is not allowed; the message is the reason code."""


def require_human(actor_id: str) -> None:
    """Only a person re-checks: a model, an agent or a system actor is refused."""
    if not actor_id or actor_id.startswith(NOT_HUMAN_PREFIXES):
        raise RecheckRefused("RECHECK_HUMAN_ONLY")


def _link(item: LinkItem) -> VerdictLink:
    link = item.record.verdict_link
    if link is None:
        raise RecheckRefused("HYPOTHESIS_LINK_MISSING")
    return link


class HypothesisLinkRechecks:
    def __init__(self, records: RequestRecords) -> None:
        self._records = records
        self._reader = HypothesisLinkReader(records.ledger)

    def _validated(
        self,
        project_id: str,
        wanted: tuple[str, ...],
        reason: RecheckReason,
        note: str,
        current_verdict_revision: str | None,
    ) -> list[LinkItem]:
        items = {item.hypothesis_id: item for item in self._reader.items(project_id, wanted)}
        if set(items) != set(wanted):
            raise RecheckRefused("HYPOTHESIS_LINK_MISSING")
        chosen = [items[hypothesis_id] for hypothesis_id in wanted]
        if any(item.assessment.state is LinkState.CURRENT for item in chosen):
            raise RecheckRefused("HYPOTHESIS_LINK_NOT_STALE")
        changes = {
            (
                _link(item).subject_kind,
                _link(item).subject_id,
                item.assessment.current_verdict_digest,
            )
            for item in chosen
        }
        if len(changes) != 1:
            raise RecheckRefused("HYPOTHESIS_LINKS_DIFFER")  # one verdict change at a time
        if any(
            item.assessment.current_verdict_revision != current_verdict_revision for item in chosen
        ):
            raise RecheckRefused("LINK_VERDICT_CHANGED_AGAIN")  # the person saw an older one
        flipped = any(item.assessment.change == "FLIPPED" for item in chosen)
        if note_required(reason, flipped) and not note.strip():
            raise RecheckRefused("RECHECK_NOTE_REQUIRED")
        return chosen

    def recheck(
        self,
        *,
        project_id: str,
        hypothesis_ids: tuple[str, ...],
        reason: RecheckReason,
        note: str,
        actor_id: str,
        current_verdict_revision: str | None,
    ) -> list[LinkRecheckEvent]:
        require_human(actor_id)
        wanted = tuple(dict.fromkeys(hypothesis_ids))
        with self._records.ledger.transaction():
            chosen = self._validated(project_id, wanted, reason, note, current_verdict_revision)
            now, batch = self._records.clock.now(), self._records.ids.new("link-recheck-batch")
            events = [self._event(item, batch, reason, note, actor_id, now) for item in chosen]
            _, earlier = self._reader.rechecks(project_id)
            self._records.save(
                project_id,
                EntityType.THREAD,
                RECHECK_KEY,
                HypothesisLinkRecheckRecord(project_id=project_id, events=(*earlier, *events)),
                actor_id,
            )
            return events

    def _event(
        self,
        item: LinkItem,
        batch: str,
        reason: RecheckReason,
        note: str,
        actor_id: str,
        now: datetime,
    ) -> LinkRecheckEvent:
        link, found = _link(item), item.assessment
        return LinkRecheckEvent(
            event_id=self._records.ids.new("link-recheck"),
            batch_id=batch,
            hypothesis_id=item.hypothesis_id,
            hypothesis_revision_digest=item.hypothesis_revision_digest,
            subject_kind=link.subject_kind,
            subject_id=link.subject_id,
            link_verdict_digest=link.verdict_digest,
            link_state=link.state,
            current_verdict_revision=found.current_verdict_revision or "",
            current_verdict_digest=found.current_verdict_digest or "",
            current_state=found.current_state or "",
            flipped=found.change == "FLIPPED",
            reason_code=reason,
            note=note.strip(),
            actor_id=actor_id,
            created_at=now,
        )
