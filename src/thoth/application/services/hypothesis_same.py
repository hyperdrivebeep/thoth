"""A person's mark that two hypotheses of different investigations are the same hypothesis.

A mark adds an event to one project record and removes nothing; taking it back is another event.
Only a person writes one, and both hypotheses must be of this project and come from an
investigation of a trace row. No model is called and nothing is paired by how sentences read.
"""

from __future__ import annotations

from thoth.application.services.human_actor import require_human_actor
from thoth.application.services.hypothesis_link_view import HypothesisLinkReader
from thoth.application.services.request_records import RequestRecords
from thoth.domain.enums import EntityType
from thoth.domain.hypothesis_same import (
    SameAction,
    SameHypothesisEvent,
    SameHypothesisRecord,
    current_pairs,
    groups,
)

KEY = "hypothesis-same"
SAME_NOT_FOUND = "SAME_NOT_FOUND"
SAME_IDENTICAL = "SAME_IDENTICAL"
SAME_NOT_INVESTIGATION = "SAME_NOT_INVESTIGATION"
SAME_ALREADY_LINKED = "SAME_ALREADY_LINKED"
SAME_NOT_LINKED = "SAME_NOT_LINKED"


class SameRefused(ValueError):
    """The mark is not written; the message is the reason code."""


def read_same(reader: HypothesisLinkReader, project_id: str) -> SameHypothesisRecord:
    content = reader.content(project_id, EntityType.THREAD, KEY)
    if content is None:
        return SameHypothesisRecord(project_id=project_id)
    return SameHypothesisRecord.model_validate(content)


class HypothesisSame:
    def __init__(self, records: RequestRecords) -> None:
        self._records = records
        self._reader = HypothesisLinkReader(records.ledger)

    def read(self, project_id: str) -> SameHypothesisRecord:
        return read_same(self._reader, project_id)

    def record(
        self,
        *,
        project_id: str,
        hypothesis_ids: tuple[str, str],
        action: SameAction,
        note: str,
        actor_id: str,
    ) -> SameHypothesisEvent:
        require_human_actor(actor_id)
        first, second = hypothesis_ids
        if first == second:
            raise SameRefused(SAME_IDENTICAL)
        pair = (first, second) if first < second else (second, first)
        with self._records.ledger.transaction():
            current = self.read(project_id)
            linked = pair in current_pairs(current)
            if action == "UNLINK":
                if not linked:
                    raise SameRefused(SAME_NOT_LINKED)
            else:
                found = [self._reader.hypothesis(project_id, item) for item in pair]
                if any(item is None for item in found):
                    raise SameRefused(SAME_NOT_FOUND)
                if any(item is not None and item.verdict_link is None for item in found):
                    raise SameRefused(SAME_NOT_INVESTIGATION)
                if linked:
                    raise SameRefused(SAME_ALREADY_LINKED)
            event = SameHypothesisEvent(
                event_id=self._records.ids.new("hypothesis-same"),
                hypothesis_ids=pair,
                action=action,
                note=note.strip(),
                actor_id=actor_id,
                created_at=self._records.clock.now(),
            )
            updated = current.model_copy(update={"events": (*current.events, event)})
            self._records.save(project_id, EntityType.THREAD, KEY, updated, actor_id)
            return event

    def view(self, project_id: str) -> dict[str, object]:
        """The current groups with what a screen shows about each member, and every mark."""
        record = self.read(project_id)
        members: dict[str, dict[str, object]] = {}
        listed = [sorted(group) for group in groups(record)]
        for hypothesis_id in dict.fromkeys(item for group in listed for item in group):
            found = self._reader.hypothesis(project_id, hypothesis_id)
            link = None if found is None else found.verdict_link
            members[hypothesis_id] = (
                {}
                if found is None or link is None
                else {
                    "statement": found.statement,
                    "investigated_at": found.created_at.isoformat(),
                    "subject_kind": link.subject_kind,
                    "subject_id": link.subject_id,
                }
            )
        return {
            "groups": listed,
            "pairs": [list(pair) for pair in sorted(current_pairs(record))],
            "members": members,
            "events": [item.model_dump(mode="json") for item in record.events],
        }
