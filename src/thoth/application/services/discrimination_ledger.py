"""Test results and refutation conditions: a person's records about a hypothesis.

A write adds an event to one project record and removes nothing. Nothing here calls a model, and
the hypothesis the model produced is never edited: what a person recorded is read beside it.
"""

from __future__ import annotations

from thoth.application.services.human_actor import require_human_actor
from thoth.application.services.hypothesis_link_view import HypothesisLinkReader
from thoth.application.services.lesson_ledger import LessonLedger
from thoth.application.services.request_records import RequestRecords
from thoth.domain.discrimination import (
    DiscriminationLedgerRecord,
    DiscriminationResult,
    MatchKind,
    RefutationConditions,
    elimination,
    latest_results,
    recorded_standing,
)
from thoth.domain.enums import EntityType
from thoth.domain.hypothesis_full import HypothesisRecord

KEY = "hypothesis-discrimination"
HYPOTHESIS_NOT_FOUND = "HYPOTHESIS_NOT_FOUND"
TEST_NOT_FOUND = "TEST_NOT_FOUND"
OBSERVATION_REQUIRED = "OBSERVATION_REQUIRED"
CONDITION_BLANK = "CONDITION_BLANK"


class DiscriminationRefused(ValueError):
    """The record is not made; the message is the reason code."""


class DiscriminationLedger:
    def __init__(self, records: RequestRecords) -> None:
        self._records = records
        self._reader = HypothesisLinkReader(records.ledger)
        self._lessons = LessonLedger(records)

    def read(self, project_id: str) -> DiscriminationLedgerRecord:
        content = self._reader.content(project_id, EntityType.THREAD, KEY)
        if content is None:
            return DiscriminationLedgerRecord(project_id=project_id)
        return DiscriminationLedgerRecord.model_validate(content)

    def _hypothesis(self, project_id: str, hypothesis_id: str) -> HypothesisRecord:
        found = self._reader.hypothesis(project_id, hypothesis_id)
        if found is None:
            raise DiscriminationRefused(HYPOTHESIS_NOT_FOUND)
        return found

    def record_result(
        self,
        *,
        project_id: str,
        hypothesis_id: str,
        test_id: str,
        observation: str,
        matched: MatchKind,
        evidence_refs: tuple[str, ...],
        actor_id: str,
    ) -> DiscriminationResult:
        require_human_actor(actor_id)
        if not observation.strip():
            raise DiscriminationRefused(OBSERVATION_REQUIRED)
        with self._records.ledger.transaction():
            hypothesis = self._hypothesis(project_id, hypothesis_id)
            details = hypothesis.generation_details
            known = () if details is None else details.discriminating_test_candidates
            if test_id not in {item.test_id for item in known}:
                raise DiscriminationRefused(TEST_NOT_FOUND)
            event = DiscriminationResult(
                event_id=self._records.ids.new("discrimination-result"),
                hypothesis_id=hypothesis_id,
                hypothesis_revision_digest=hypothesis.revision_digest,
                test_id=test_id,
                observation=observation.strip(),
                matched=matched,
                evidence_refs=tuple(item.strip() for item in evidence_refs if item.strip()),
                actor_id=actor_id,
                created_at=self._records.clock.now(),
            )
            current = self.read(project_id)
            self._save(current.model_copy(update={"results": (*current.results, event)}), actor_id)
            # the result is a lesson in the context of its row, in the same commit
            self._lessons.after_result(project_id, hypothesis, current.results, event, actor_id)
            return event

    def record_conditions(
        self, *, project_id: str, hypothesis_id: str, conditions: tuple[str, ...], actor_id: str
    ) -> RefutationConditions:
        require_human_actor(actor_id)
        if any(not item.strip() for item in conditions):
            raise DiscriminationRefused(CONDITION_BLANK)
        with self._records.ledger.transaction():
            self._hypothesis(project_id, hypothesis_id)
            event = RefutationConditions(
                event_id=self._records.ids.new("refutation-conditions"),
                hypothesis_id=hypothesis_id,
                conditions=tuple(item.strip() for item in conditions),
                actor_id=actor_id,
                created_at=self._records.clock.now(),
            )
            current = self.read(project_id)
            self._save(
                current.model_copy(update={"conditions": (*current.conditions, event)}), actor_id
            )
            return event

    def _save(self, record: DiscriminationLedgerRecord, actor_id: str) -> None:
        self._records.save(record.project_id, EntityType.THREAD, KEY, record, actor_id)

    def view(self, project_id: str, only: str | None = None) -> list[dict[str, object]]:
        """What was recorded for each hypothesis, with the elimination the results amount to."""
        record = self.read(project_id)
        named = [item.hypothesis_id for item in (*record.results, *record.conditions)]
        # each hypothesis' own records, grouped once rather than searched for in the whole record
        results: dict[str, list[DiscriminationResult]] = {}
        for result in record.results:
            results.setdefault(result.hypothesis_id, []).append(result)
        conditions: dict[str, list[RefutationConditions]] = {}
        for entry in record.conditions:
            conditions.setdefault(entry.hypothesis_id, []).append(entry)
        found: list[dict[str, object]] = []
        for hypothesis_id in dict.fromkeys(named):
            if only is not None and hypothesis_id != only:
                continue
            own = tuple(results.get(hypothesis_id, ()))
            listed = conditions.get(hypothesis_id, [])
            found.append(
                {
                    "hypothesis_id": hypothesis_id,
                    "results": [
                        item.model_dump(mode="json")
                        for item in latest_results(own, hypothesis_id).values()
                    ],
                    "result_history_count": len(own),
                    "refutation_conditions": list(listed[-1].conditions) if listed else [],
                    "conditions_history_count": len(listed),
                    "elimination": elimination(own, hypothesis_id),
                    "standing": recorded_standing(own, hypothesis_id),
                }
            )
        return found
