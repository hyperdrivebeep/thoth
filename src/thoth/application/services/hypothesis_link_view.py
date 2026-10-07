"""Read hypothesis links against today's trace, from the ledger alone.

Every question here is answered from committed records (hypothesis heads, the trace record and its
verdict revisions, the re-check record), so the same answer is available to a screen, to the
approval path and to the execution checks without handing each of them more stores.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import cast

from pydantic import ValidationError

from thoth.application.services.verification_trace import KEY as TRACE_KEY
from thoth.application.services.verification_trace import REVISION_PREFIX, mark_stale
from thoth.domain.enums import EntityType
from thoth.domain.hypothesis_full import HypothesisRecord
from thoth.domain.verdict_link import (
    CurrentVerdict,
    HypothesisLinkRecheckRecord,
    LinkAssessment,
    LinkRecheckEvent,
    LinkState,
    VerdictLink,
    assess_link,
    verdict_core_digest,
)
from thoth.domain.verification_trace import (
    SubjectKind,
    VerificationTraceRecord,
    VerificationVerdictRecord,
)
from thoth.ports.ledger import LedgerPort

RECHECK_KEY = "hypothesis-link-rechecks"
HYPOTHESIS_BASIS_CHANGED = "HYPOTHESIS_BASIS_CHANGED"


@dataclass(frozen=True)
class LinkItem:
    hypothesis_id: str
    hypothesis_revision_digest: str
    statement: str
    subject_title: str
    record: HypothesisRecord
    assessment: LinkAssessment


def _items(value: object) -> list[object]:
    return (
        list(cast(list[object] | tuple[object, ...], value))
        if isinstance(value, (list, tuple))
        else []
    )


class HypothesisLinkReader:
    def __init__(self, ledger: LedgerPort) -> None:
        self._ledger = ledger

    def content(
        self,
        project_id: str,
        entity_type: EntityType,
        entity_id: str,
        heads: Mapping[str, str] | None = None,
    ) -> dict[str, object] | None:
        """The current content of one entity; `heads` is the project's heads when already read."""
        digest = (self._ledger.read_heads(project_id) if heads is None else heads).get(
            f"{entity_type.value}:{entity_id}"
        )
        return None if digest is None else self.content_at(project_id, digest)

    def content_at(self, project_id: str, digest: str) -> dict[str, object] | None:
        revision = self._ledger.read_revision_by_digest(project_id, digest)
        snapshot = None if revision is None else self._ledger.read_snapshot(revision.snapshot_id)
        return None if snapshot is None else dict(snapshot.content)

    def trace(self, project_id: str) -> tuple[str | None, VerificationTraceRecord | None]:
        digest = self._ledger.read_heads(project_id).get(f"{EntityType.THREAD.value}:{TRACE_KEY}")
        content = None if digest is None else self.content_at(project_id, digest)
        return digest, None if content is None else VerificationTraceRecord.model_validate(content)

    def current_verdicts(
        self,
        project_id: str,
        record: VerificationTraceRecord | None,
        only: Collection[tuple[SubjectKind, str]] | None = None,
    ) -> dict[tuple[SubjectKind, str], CurrentVerdict]:
        """The verdict of each row the trace still shows; a row that left the set is not here.

        `only` limits it to the rows a reader will look at, so the others are not read at all.
        """
        if record is None:
            return {}
        shown = mark_stale(record.trace_set, record.pending_changes)
        found: dict[tuple[SubjectKind, str], CurrentVerdict] = {}
        heads = self._ledger.read_heads(project_id)  # once, not once per row
        for key, revision_digest in record.current_digests().items():
            if key not in shown or (only is not None and key not in only):
                continue
            content = self.content(
                project_id, EntityType.THREAD, REVISION_PREFIX + revision_digest, heads
            )
            if content is None:
                continue
            revision = VerificationVerdictRecord.model_validate(content).revision
            found[key] = CurrentVerdict(
                verdict_revision=revision.revision_digest,
                verdict_digest=revision.verdict_digest,
                state=revision.state.value,
                core_digest=verdict_core_digest(revision),
            )
        return found

    def link_core_digest(self, project_id: str, link: VerdictLink) -> str | None:
        """The core of the verdict the hypothesis stood on, read from its stored revision."""
        content = self.content(
            project_id, EntityType.THREAD, REVISION_PREFIX + link.verdict_revision
        )
        if content is None:
            return None
        return verdict_core_digest(VerificationVerdictRecord.model_validate(content).revision)

    def rechecks(self, project_id: str) -> tuple[str | None, tuple[LinkRecheckEvent, ...]]:
        digest = self._ledger.read_heads(project_id).get(f"{EntityType.THREAD.value}:{RECHECK_KEY}")
        content = None if digest is None else self.content_at(project_id, digest)
        if content is None:
            return digest, ()
        return digest, HypothesisLinkRecheckRecord.model_validate(content).events

    def hypothesis(self, project_id: str, hypothesis_id: str) -> HypothesisRecord | None:
        """The current revision of one hypothesis, or None when there is no such hypothesis."""
        content = self.content(project_id, EntityType.HYPOTHESIS, hypothesis_id)
        if content is None or "hypothesis_revision_id" not in content:
            return None  # a portfolio shares the entity type
        try:
            return HypothesisRecord.model_validate(content)
        except ValidationError:
            return None

    def linked_hypotheses(
        self, project_id: str, only: tuple[str, ...] | None = None
    ) -> list[HypothesisRecord]:
        """The current revision of each hypothesis that has a verdict link."""
        found: list[HypothesisRecord] = []
        for key, digest in sorted(self._ledger.read_heads(project_id).items()):
            if not key.startswith(f"{EntityType.HYPOTHESIS.value}:"):
                continue
            if only is not None and key.split(":", 1)[1] not in only:
                continue
            content = self.content_at(project_id, digest)
            if content is None or "hypothesis_revision_id" not in content:
                continue  # a portfolio shares the entity type
            try:
                record = HypothesisRecord.model_validate(content)
            except ValidationError:
                continue
            if record.verdict_link is not None:
                found.append(record)
        return found

    def items(self, project_id: str, only: tuple[str, ...] | None = None) -> list[LinkItem]:
        records = self.linked_hypotheses(project_id, only)
        if not records:
            return []
        _, trace = self.trace(project_id)
        titles = {} if trace is None else {i.item_id: i.title for i in trace.trace_set.items}
        verdicts = self.current_verdicts(
            project_id,
            trace,
            {
                (SubjectKind(item.verdict_link.subject_kind), item.verdict_link.subject_id)
                for item in records
                if item.verdict_link is not None
            },
        )
        _, events = self.rechecks(project_id)
        items: list[LinkItem] = []
        for record in records:
            link = record.verdict_link
            assert link is not None
            current = verdicts.get((SubjectKind(link.subject_kind), link.subject_id))
            moved = current is not None and current.verdict_digest != link.verdict_digest
            items.append(
                LinkItem(
                    hypothesis_id=record.hypothesis_id,
                    hypothesis_revision_digest=record.revision_digest,
                    statement=record.statement,
                    subject_title=titles.get(link.subject_id, ""),
                    record=record,
                    assessment=assess_link(
                        link,
                        current,
                        events,
                        hypothesis_id=record.hypothesis_id,
                        link_core_digest=self.link_core_digest(project_id, link) if moved else None,
                    ),
                )
            )
        return items

    def reason_distribution(self, project_id: str) -> dict[str, object]:
        """Why people re-checked, project-wide: events per reason, and how many were flips."""
        _, events = self.rechecks(project_id)
        return {
            "total": len(events),
            "flipped": sum(1 for event in events if event.flipped),
            "by_reason": dict(Counter(event.reason_code for event in events)),
        }

    def stale_hypothesis_ids(
        self, project_id: str, hypothesis_ids: tuple[str, ...]
    ) -> tuple[str, ...]:
        """Of these hypotheses, those whose link says they stand on a verdict that has changed."""
        wanted = tuple(dict.fromkeys(hypothesis_ids))
        if not wanted:
            return ()
        return tuple(
            item.hypothesis_id
            for item in self.items(project_id, wanted)
            if item.assessment.state is LinkState.STALE
        )

    def action_hypotheses(self, project_id: str, key: str) -> tuple[str, ...]:
        """The hypotheses an action, or the actions of a plan, rest on ("ACTION:<id>" keys)."""
        content = self.content(project_id, EntityType.ACTION, key.split(":", 1)[-1])
        if content is None:
            return ()
        refs = content.get("hypothesis_refs")
        found = [str(item) for item in _items(refs)]
        for action in _items(content.get("selected_action_refs")):
            found.extend(self.action_hypotheses(project_id, f"ACTION:{action}"))
        return tuple(dict.fromkeys(found))

    def require_action_hypotheses_current(self, project_id: str, key: str) -> None:
        """Refuse an action or plan that rests on a hypothesis whose verdict has changed."""
        if self.stale_hypothesis_ids(project_id, self.action_hypotheses(project_id, key)):
            raise ValueError(HYPOTHESIS_BASIS_CHANGED)

    def require_plan_revision_hypotheses_current(
        self, project_id: str, plan_revision_digest: str
    ) -> None:
        """The same check for a stored plan revision (the approval path names it by digest)."""
        content = self.content_at(project_id, plan_revision_digest)
        if content is None:
            return
        found: list[str] = []
        for action in _items(content.get("selected_action_refs")):
            found.extend(self.action_hypotheses(project_id, f"ACTION:{action}"))
        if self.stale_hypothesis_ids(project_id, tuple(found)):
            raise ValueError(HYPOTHESIS_BASIS_CHANGED)
