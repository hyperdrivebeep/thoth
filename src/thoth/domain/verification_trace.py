"""A trace from requirement to result, and the verdicts a rule computes from it.

A requirement is refined by criteria; a criterion has one rule (comparator, threshold, unit,
condition); a test case produces results; a result points at one criterion. The verdict of a
criterion is computed from the rule and the chosen result, never by a model. A verdict has three
separate parts: the computed state, whether it still matches today's inputs (currentness), and
whether a person confirmed it (confirmation). Old verdict revisions are kept, each linked to the
one before it.

Ids are text exactly as the user wrote them ("007" stays "007"). Numbers that decide a verdict are
Decimal, never binary floats.
"""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.canonical import canonical_payload, domain_digest

TRACE_SCHEMA_VERSION = "1.0.0"
SYSTEM_ACTOR = "system:verification-trace"


class TraceKind(StrEnum):
    REQUIREMENT = "REQUIREMENT"
    CRITERION = "CRITERION"
    TEST_CASE = "TEST_CASE"
    RESULT = "RESULT"
    EVIDENCE = "EVIDENCE"


class TraceRelation(StrEnum):
    REFINES = "REFINES"
    VERIFIED_BY = "VERIFIED_BY"
    PRODUCES = "PRODUCES"
    SUPPORTED_BY = "SUPPORTED_BY"


# relation -> (kind of the "from" item, kind of the "to" item). The owner of a link is "from".
LINK_ENDPOINTS: dict[TraceRelation, tuple[TraceKind, TraceKind]] = {
    TraceRelation.REFINES: (TraceKind.CRITERION, TraceKind.REQUIREMENT),
    TraceRelation.VERIFIED_BY: (TraceKind.CRITERION, TraceKind.TEST_CASE),
    TraceRelation.PRODUCES: (TraceKind.TEST_CASE, TraceKind.RESULT),
    TraceRelation.SUPPORTED_BY: (TraceKind.RESULT, TraceKind.EVIDENCE),
}


class Comparator(StrEnum):
    AT_LEAST = ">="
    AT_MOST = "<="


class SelectionPolicyName(StrEnum):
    LATEST_PER_CONDITION = "LATEST_PER_CONDITION"
    ALL_MUST_PASS = "ALL_MUST_PASS"


class SubjectKind(StrEnum):
    CRITERION = "CRITERION"
    REQUIREMENT = "REQUIREMENT"


class CriterionVerdictState(StrEnum):
    PASS_COMPUTED = "PASS_COMPUTED"
    FAIL_COMPUTED = "FAIL_COMPUTED"
    HOLD_NO_RESULT = "HOLD_NO_RESULT"
    HOLD_INVALID_RESULT = "HOLD_INVALID_RESULT"
    HOLD_NO_RULE = "HOLD_NO_RULE"


class RequirementVerdictState(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    FAIL_WITH_INCOMPLETE_COVERAGE = "FAIL_WITH_INCOMPLETE_COVERAGE"
    HOLD_INCOMPLETE = "HOLD_INCOMPLETE"
    HOLD_NO_CRITERIA = "HOLD_NO_CRITERIA"


class CurrentnessState(StrEnum):
    CURRENT = "CURRENT"
    STALE_BASIS = "STALE_BASIS"


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("TRACE_TIMESTAMP_NEEDS_TIMEZONE")
    return value


def _require_finite(value: Decimal | None) -> Decimal | None:
    if value is not None and not value.is_finite():
        raise ValueError("TRACE_DECIMAL_NOT_FINITE")
    return value


class SelectionPolicy(DomainModel):
    name: SelectionPolicyName = SelectionPolicyName.LATEST_PER_CONDITION
    version: int = Field(default=1, ge=1)


class Rounding(DomainModel):
    """How a measured value is rounded before it is compared with the threshold."""

    digits: int = Field(default=2, ge=0, le=9)
    mode: Literal["HALF_UP", "HALF_EVEN"] = "HALF_UP"

    def apply(self, value: Decimal) -> Decimal:
        rule = ROUND_HALF_UP if self.mode == "HALF_UP" else ROUND_HALF_EVEN
        try:
            return value.quantize(Decimal(1).scaleb(-self.digits), rounding=rule)
        except InvalidOperation as exc:
            raise ValueError("TRACE_VALUE_OUT_OF_RANGE") from exc


class TraceItem(DomainModel):
    item_id: str = Field(min_length=1)  # the user's id, kept exactly (leading zeros included)
    item_key: str = Field(min_length=1)  # internal fixed key, never shown as the id
    kind: TraceKind
    title: str = ""
    fields: dict[str, str] = Field(default_factory=dict)
    source_span_refs: tuple[str, ...] = ()

    @field_validator("item_id")
    @classmethod
    def _id_is_exact(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("TRACE_ID_HAS_EDGE_WHITESPACE")
        return value


class TraceLink(DomainModel):
    link_id: str = Field(min_length=1)
    from_id: str = Field(min_length=1)
    to_id: str = Field(min_length=1)
    relation: TraceRelation


class CriterionRule(DomainModel):
    rule_id: str = Field(min_length=1)
    rule_revision: int = Field(default=1, ge=1)
    criterion_id: str = Field(min_length=1)
    measure: str = Field(min_length=1)
    comparator: Comparator
    threshold: Decimal
    unit: str = Field(min_length=1)
    condition: str = Field(min_length=1)
    rounding: Rounding = Field(default_factory=Rounding)
    required: bool = True

    check_finite = field_validator("threshold")(_require_finite)


class ResultRecord(DomainModel):
    result_id: str = Field(min_length=1)
    result_revision: int = Field(default=1, ge=1)
    criterion_id: str = Field(min_length=1)
    condition: str = Field(min_length=1)
    value: Decimal | None = None
    raw_value: str | None = None  # what was written, kept when it is not a number
    unit: str = ""
    numerator: int | None = None
    denominator: int | None = None
    observed_at: datetime
    source_span_refs: tuple[str, ...] = ()

    check_finite = field_validator("value")(_require_finite)
    check_aware = field_validator("observed_at")(_require_aware)


class TraceSet(DomainModel):
    """Everything the verdicts are computed from. Order of the lists does not matter."""

    items: tuple[TraceItem, ...] = ()
    links: tuple[TraceLink, ...] = ()
    rules: tuple[CriterionRule, ...] = ()
    results: tuple[ResultRecord, ...] = ()

    @model_validator(mode="after")
    def _graph_is_consistent(self) -> Self:
        _unique("TRACE_ITEM_ID_DUPLICATE", [item.item_id for item in self.items])
        _unique("TRACE_ITEM_KEY_DUPLICATE", [item.item_key for item in self.items])
        _unique("TRACE_LINK_ID_DUPLICATE", [link.link_id for link in self.links])
        _unique("TRACE_RULE_ID_DUPLICATE", [rule.rule_id for rule in self.rules])
        _unique("TRACE_RULE_CRITERION_DUPLICATE", [rule.criterion_id for rule in self.rules])
        _unique("TRACE_RESULT_ID_DUPLICATE", [result.result_id for result in self.results])
        kinds = {item.item_id: item.kind for item in self.items}
        for link in self.links:
            expected = LINK_ENDPOINTS[link.relation]
            if (kinds.get(link.from_id), kinds.get(link.to_id)) != expected:
                raise ValueError(f"TRACE_LINK_ENDPOINT_INVALID:{link.link_id}")
        for rule in self.rules:
            if kinds.get(rule.criterion_id) is not TraceKind.CRITERION:
                raise ValueError(f"TRACE_RULE_TARGET_INVALID:{rule.rule_id}")
        for result in self.results:
            if kinds.get(result.result_id) is not TraceKind.RESULT:
                raise ValueError(f"TRACE_RESULT_ITEM_MISSING:{result.result_id}")
            if kinds.get(result.criterion_id) is not TraceKind.CRITERION:
                raise ValueError(f"TRACE_RESULT_TARGET_INVALID:{result.result_id}")
        return self

    @property
    def set_digest(self) -> str:
        paths = frozenset({("items",), ("links",), ("rules",), ("results",)})
        return domain_digest(
            "TRACE_SET", TRACE_SCHEMA_VERSION, canonical_payload(self, set_paths=paths)
        )


def _unique(code: str, values: list[str]) -> None:
    if len(values) != len(set(values)):
        raise ValueError(code)


class ChangedDependency(DomainModel):
    """One change to something a verdict depends on, with before and after."""

    kind: Literal["RULE", "RESULT", "LINK", "POLICY"]
    ref_id: str
    field: str  # a field name, or "*" when the whole thing was added or removed
    before: str | None = None
    after: str | None = None
    before_revision: int | None = None
    after_revision: int | None = None
    criterion_ids: tuple[str, ...] = ()
    requirement_ids: tuple[str, ...] = ()


class Currentness(DomainModel):
    state: CurrentnessState = CurrentnessState.CURRENT
    changed_dependencies: tuple[ChangedDependency, ...] = ()


class CauseOfChange(DomainModel):
    trigger: str
    changed: tuple[ChangedDependency, ...] = ()


class VerdictBasis(DomainModel):
    input_digest: str
    source_span_refs: tuple[str, ...] = ()
    # The trace keeps references to where the evidence is, not the evidence text itself.
    evidence_text_preserved: bool = False
    child_verdict_digests: tuple[str, ...] = ()


class ResultCandidate(DomainModel):
    result_id: str
    result_revision: int


class ExcludedResult(DomainModel):
    result_id: str
    result_revision: int
    reason: Literal["CONDITION_MISMATCH", "SUPERSEDED_BY_LATER_RESULT"]


class ResultSelection(DomainModel):
    policy: SelectionPolicy
    candidates: tuple[ResultCandidate, ...] = ()
    chosen: tuple[ResultCandidate, ...] = ()
    excluded: tuple[ExcludedResult, ...] = ()


class AppliedConditions(DomainModel):
    required: bool = True
    rule_id: str | None = None
    rule_revision: int | None = None
    condition: str | None = None
    unit: str | None = None
    comparator: Comparator | None = None
    threshold: Decimal | None = None
    rounding: Rounding | None = None
    required_criteria: tuple[str, ...] = ()  # for a requirement: the criteria that decide it


class VerdictActor(DomainModel):
    actor_id: str = SYSTEM_ACTOR
    computed_at: datetime

    check_aware = field_validator("computed_at")(_require_aware)


class VerdictReasons(DomainModel):
    computed: tuple[str, ...] = ()  # codes found by the rule: violations and what is missing
    human: tuple[str, ...] = ()  # words a person wrote; never produced by the computation


VerdictState = CriterionVerdictState | RequirementVerdictState


def verdict_digest_of(item: VerdictDraft) -> str:
    """What the verdict says, without who computed it, when, or why it was recomputed."""
    payload: dict[str, object] = {
        "subject_kind": item.subject_kind,
        "subject_id": item.subject_id,
        "state": item.state,
        "basis": item.basis,
        "selection": item.selection,
        "conditions": item.conditions,
        "computed_reasons": item.reasons.computed,
    }
    return domain_digest("TRACE_VERDICT", TRACE_SCHEMA_VERSION, canonical_payload(payload))


def revision_digest_of(item: VerdictDraft, verdict_digest: str, parent_digest: str | None) -> str:
    payload: dict[str, object] = {
        "verdict_digest": verdict_digest,
        "parent_digest": parent_digest,
        "cause": item.cause,
        "actor": item.actor,
        "human_reasons": item.reasons.human,
    }
    return domain_digest("TRACE_VERDICT_REVISION", TRACE_SCHEMA_VERSION, canonical_payload(payload))


class VerdictDraft(DomainModel):
    """A computed verdict before it is given its digests and its place in the history."""

    subject_kind: SubjectKind
    subject_id: str
    state: VerdictState
    cause: CauseOfChange  # 1. what changed and what triggered the recompute
    basis: VerdictBasis  # 2. what it stood on at the time
    selection: ResultSelection | None = None  # 3. which results were looked at (criteria only)
    conditions: AppliedConditions  # 4. condition, unit, required set that applied
    actor: VerdictActor  # 5. who computed it and when (a person's confirmation is separate)
    reasons: VerdictReasons  # 6. computed reasons apart from human-written ones

    @model_validator(mode="after")
    def _state_matches_subject(self) -> Self:
        allowed = (
            CriterionVerdictState
            if self.subject_kind is SubjectKind.CRITERION
            else RequirementVerdictState
        )
        if type(self.state) is not allowed:
            raise ValueError("TRACE_VERDICT_STATE_WRONG_KIND")
        return self


class VerdictRevision(VerdictDraft):
    """One computed verdict with the six things needed to explain it later."""

    parent_digest: str | None = None  # the revision this one follows; old ones are never deleted
    verdict_digest: str
    revision_digest: str

    @model_validator(mode="after")
    def _digests_hold(self) -> Self:
        if self.verdict_digest != verdict_digest_of(self):
            raise ValueError("TRACE_VERDICT_DIGEST_MISMATCH")
        expected = revision_digest_of(self, self.verdict_digest, self.parent_digest)
        if self.revision_digest != expected:
            raise ValueError("TRACE_VERDICT_REVISION_DIGEST_MISMATCH")
        return self


class Confirmation(DomainModel):
    """A person looked at one verdict revision. It does not change the verdict and is not copied
    to a recomputed one."""

    verdict_revision_digest: str
    actor_id: str = Field(min_length=1)
    confirmed_at: datetime
    rationale: str = Field(min_length=1)

    check_aware = field_validator("confirmed_at")(_require_aware)


def subject_key(kind: SubjectKind, subject_id: str) -> str:
    return f"{kind.value}:{subject_id}"


class VerificationVerdictRecord(DomainModel):
    """One verdict revision, stored once under its own digest and never changed."""

    record_kind: Literal["VerificationVerdictRecord"] = "VerificationVerdictRecord"
    schema_version: Literal["2.0.0"] = "2.0.0"
    project_id: str
    revision: VerdictRevision


class VerificationTraceRecord(DomainModel):
    """The stored state of a project's trace: the set, which verdict revisions belong to each
    subject (their digests, oldest first) and the confirmations. The revisions themselves are
    separate records, so saving the trace does not copy the whole history again."""

    record_kind: Literal["VerificationTraceRecord"] = "VerificationTraceRecord"
    schema_version: Literal["2.0.0"] = "2.0.0"
    project_id: str
    trace_set: TraceSet
    policy: SelectionPolicy = Field(default_factory=SelectionPolicy)
    history: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    confirmations: tuple[Confirmation, ...] = ()
    pending_changes: tuple[ChangedDependency, ...] = ()  # changes not yet recomputed

    def current_digests(self) -> dict[tuple[SubjectKind, str], str]:
        found: dict[tuple[SubjectKind, str], str] = {}
        for key, digests in self.history.items():
            kind, _, subject_id = key.partition(":")
            if digests:
                found[(SubjectKind(kind), subject_id)] = digests[-1]
        return found

    def confirmations_of(self, revision_digest: str) -> tuple[Confirmation, ...]:
        return tuple(
            item for item in self.confirmations if item.verdict_revision_digest == revision_digest
        )


def seal_verdict(draft: VerdictDraft, parent_digest: str | None) -> VerdictRevision:
    """Give a draft its two digests and link it to the revision it follows."""
    verdict_digest = verdict_digest_of(draft)
    return VerdictRevision(
        subject_kind=draft.subject_kind,
        subject_id=draft.subject_id,
        state=draft.state,
        cause=draft.cause,
        basis=draft.basis,
        selection=draft.selection,
        conditions=draft.conditions,
        actor=draft.actor,
        reasons=draft.reasons,
        parent_digest=parent_digest,
        verdict_digest=verdict_digest,
        revision_digest=revision_digest_of(draft, verdict_digest, parent_digest),
    )
