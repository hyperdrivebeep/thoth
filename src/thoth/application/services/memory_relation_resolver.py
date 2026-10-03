"""Settle how a new memory relates to the stored ones, holding what the rules cannot settle.

A relation the rules call AMBIGUOUS may be put to a model at most `limit` times per investigation.
Without a model, over the limit, without quoted evidence or on a failure the memory is held; a
model's answer is only a proposal and never overrides the rules.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from thoth.application.services.memory_relation import MemorySubject, classify_memory_relation
from thoth.application.services.memory_shape import (
    RecordShape,
    key_fields,
    member_keys,
    record_shape,
)
from thoth.domain.enums import MemoryKind
from thoth.domain.memory import FullMemoryRevision, MemoryRecord, MemoryTransition
from thoth.domain.memory_relation import (
    MemoryRelation,
    MemoryRelationJudgment,
    MemoryRelationQuestion,
    MemoryRelationText,
    RelationOutcome,
)
from thoth.ports.ledger import LedgerPort
from thoth.ports.memory import MemoryRelationJudgePort

MAX_MODEL_RELATION_CALLS = 3
_TEXT = 1_000
_LINEAGE_DEPTH = 32


@dataclass
class MemoryRelationBudget:
    """Model relation calls left for one investigation."""

    limit: int = MAX_MODEL_RELATION_CALLS
    used: int = 0


@dataclass(frozen=True)
class RelationVerdict:
    contradiction: bool = False
    ambiguous: bool = False
    judgments: tuple[MemoryRelationJudgment, ...] = ()

    @property
    def conflict(self) -> bool:
        return self.contradiction or self.ambiguous


def _clean(text: str) -> str:
    return " ".join(text.split())


def _quoted(span: str, text: str) -> bool:
    return bool(span.strip()) and _clean(span).casefold() in _clean(text).casefold()


def correction_root(digest: str, by_digest: Mapping[str, FullMemoryRevision]) -> str:
    """The stored version a chain of corrections started from."""

    seen = {digest}
    while digest in by_digest:
        parent = by_digest[digest].parent_revision_digest
        if parent is None or parent in seen:
            break
        seen.add(parent)
        digest = parent
    return digest


class MemoryRelationResolver:
    def __init__(self, ledger: LedgerPort, judge: MemoryRelationJudgePort | None = None) -> None:
        self._ledger = ledger
        self._judge = judge

    def candidate_subject(
        self,
        *,
        project_id: str,
        memory_id: str,
        kind: MemoryKind,
        owner_revision_ref: str,
        source_ref: str | None,
        scope: Mapping[str, str],
        content: Mapping[str, object] | None,
        assertion: str | None,
        staged_parents: Mapping[str, tuple[str, ...]],
        correction_root: str | None,
    ) -> MemorySubject:
        return MemorySubject(
            memory_id=memory_id,
            kind=kind,
            owner_revision_ref=owner_revision_ref,
            source_ref=source_ref,
            scope=dict(scope),
            shape=RecordShape.OTHER if content is None else record_shape(content),
            member_keys=frozenset() if content is None else member_keys(content),
            fields={} if content is None else key_fields(content),
            owner_lineage=self._lineage(project_id, owner_revision_ref, staged_parents),
            assertion=assertion,
            correction_root=correction_root,
        )

    def stored_subject(
        self, item: FullMemoryRevision, by_digest: Mapping[str, FullMemoryRevision]
    ) -> MemorySubject:
        return self.candidate_subject(
            project_id=item.project_id,
            memory_id=item.memory_id,
            kind=item.kind,
            owner_revision_ref=item.owner_revision_ref,
            source_ref=item.source_ref,
            scope=item.scope,
            content=None if item.assertion is not None else self._content(item),
            assertion=item.assertion,
            staged_parents={},
            correction_root=None
            if item.parent_revision_digest is None
            else correction_root(item.parent_revision_digest, by_digest),
        )

    async def resolve(
        self,
        new: MemorySubject,
        new_text: str,
        existing: Sequence[FullMemoryRevision],
        by_digest: Mapping[str, FullMemoryRevision],
        budget: MemoryRelationBudget,
        subjects: dict[str, MemorySubject] | None = None,
    ) -> RelationVerdict:
        known = {} if subjects is None else subjects
        contradiction = ambiguous = False
        judgments: list[MemoryRelationJudgment] = []
        for item in existing:
            if item.transition != MemoryTransition.COMMIT or item.kind != new.kind:
                continue
            if item.revision_digest not in known:
                known[item.revision_digest] = self.stored_subject(item, by_digest)
            relation = classify_memory_relation(new, known[item.revision_digest])
            if relation == MemoryRelation.CONTRADICTION_CANDIDATE:
                contradiction = True
            elif relation == MemoryRelation.AMBIGUOUS:
                judgment = await self._judge_pair(new, new_text, item, budget)
                judgments.append(judgment)
                if judgment.outcome != "MODEL_APPLIED":
                    ambiguous = True
                elif judgment.proposed_relation == MemoryRelation.CONTRADICTION_CANDIDATE:
                    contradiction = True
        return RelationVerdict(contradiction, ambiguous, tuple(judgments))

    async def resolve_candidate(
        self,
        candidate: MemoryRecord,
        *,
        content: Mapping[str, object] | None,
        text: str,
        scope: Mapping[str, str],
        staged_parents: Mapping[str, tuple[str, ...]],
        parent_revision_digest: str | None,
        existing: Sequence[FullMemoryRevision],
        readable: Sequence[FullMemoryRevision],
        budget: MemoryRelationBudget,
        subjects: dict[str, MemorySubject],
    ) -> RelationVerdict:
        """How a candidate relates to the stored memories it may be compared with."""

        by_digest = {item.revision_digest: item for item in readable}
        root = (
            None
            if parent_revision_digest is None
            else correction_root(parent_revision_digest, by_digest)
        )
        new = self.candidate_subject(
            project_id=candidate.project_id,
            memory_id=candidate.memory_id,
            kind=candidate.kind,
            owner_revision_ref=candidate.owner_revision_ref,
            source_ref=candidate.source_ref,
            scope=scope,
            content=None if candidate.assertion is not None else content,
            assertion=candidate.assertion,
            staged_parents=staged_parents,
            correction_root=root,
        )
        return await self.resolve(new, text, existing, by_digest, budget, subjects)

    async def _judge_pair(
        self,
        new: MemorySubject,
        new_text: str,
        item: FullMemoryRevision,
        budget: MemoryRelationBudget,
    ) -> MemoryRelationJudgment:
        # Which memory the model sees first is not fixed; it is derived and recorded.
        digest = hashlib.sha256(f"{new.memory_id}|{item.memory_id}".encode()).digest()
        order = "CANDIDATE_FIRST" if digest[0] % 2 == 0 else "EXISTING_FIRST"

        def record(
            outcome: RelationOutcome,
            proposed: MemoryRelation | None = None,
            reason: str | None = None,
        ) -> MemoryRelationJudgment:
            return MemoryRelationJudgment(
                memory_id=new.memory_id,
                other_memory_id=item.memory_id,
                order=order,
                outcome=outcome,
                proposed_relation=proposed,
                reason=reason,
            )

        if self._judge is None:
            return record("HELD_NO_MODEL")
        if budget.used >= budget.limit:
            return record("HELD_OVER_LIMIT")
        budget.used += 1
        mine = MemoryRelationText(memory_id=new.memory_id, text=_clean(new_text)[:_TEXT])
        theirs = MemoryRelationText(
            memory_id=item.memory_id, text=_clean(item.assertion or item.content_excerpt)[:_TEXT]
        )
        first, second = (mine, theirs) if order == "CANDIDATE_FIRST" else (theirs, mine)
        try:
            proposal = await self._judge.judge(MemoryRelationQuestion(first=first, second=second))
        except Exception:
            return record("HELD_MODEL_FAILED")
        cited = _quoted(proposal.first_span, first.text) and _quoted(
            proposal.second_span, second.text
        )
        if not cited:
            return record("HELD_NO_EVIDENCE", proposal.relation)
        if proposal.relation == MemoryRelation.AMBIGUOUS:
            return record("MODEL_UNDECIDED", proposal.relation, proposal.reason)
        return record("MODEL_APPLIED", proposal.relation, proposal.reason)

    def _content(self, item: FullMemoryRevision) -> Mapping[str, object] | None:
        owner = self._ledger.read_revision_by_digest(item.project_id, item.owner_revision_ref)
        snapshot = None if owner is None else self._ledger.read_snapshot(owner.snapshot_id)
        return None if snapshot is None else snapshot.content

    def _lineage(
        self, project_id: str, digest: str, staged: Mapping[str, tuple[str, ...]]
    ) -> frozenset[str]:
        """The record versions a version descends from, itself included."""

        seen: set[str] = set()
        pending = [digest]
        while pending and len(seen) < _LINEAGE_DEPTH:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            parents = staged.get(current)
            if parents is None:
                revision = self._ledger.read_revision_by_digest(project_id, current)
                parents = () if revision is None else revision.parent_revision_digests
            pending.extend(parents)
        return frozenset(seen)
