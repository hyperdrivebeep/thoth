"""Bounded executable policies; fixed authority and scientific guards stay outside them."""

import re
from typing import Annotated, Any, Literal, TypeGuard, cast

from pydantic import (
    Discriminator,
    Field,
    SerializerFunctionWrapHandler,
    Tag,
    model_serializer,
    model_validator,
)

from thoth.domain.base import DomainModel
from thoth.domain.enums import AuthorityState, CutoffState
from thoth.domain.evidence import EvidenceSpan, connected_retrieval_spans
from thoth.domain.glossary import GlossaryConfidence, GlossaryEntry, entry_matches_its_id
from thoth.domain.ids import Sha256


class PromptBehaviorPolicy(DomainModel):
    kind: Literal["PROMPT_BUNDLE"] = "PROMPT_BUNDLE"
    version: Literal["2.0.0"] = "2.0.0"
    task_guidance: str = Field(default="", max_length=5000)


class RetrievalBehaviorPolicy(DomainModel):
    kind: Literal["RETRIEVAL_POLICY"] = "RETRIEVAL_POLICY"
    version: Literal["2.0.0"] = "2.0.0"
    max_spans: int = Field(default=160, ge=1, le=160)
    character_budget: int = Field(default=50000, ge=1, le=50000)


type LaneId = Literal["LEXICAL_SUBSTRING_V1", "PROTECTED_EXACT", "GROUPED_BM25", "CHAR_BIGRAM"]
# A fused policy may also name the glossary lane. 3.0.0 does not: its lanes stay as they were.
type FusedLaneId = LaneId | Literal["GLOSSARY_BM25"]


class RetrievalLaneSpec(DomainModel):
    """One named ranking rule of a retrieval policy.

    The rule the research path has always used is the default, so a stored policy that names it
    keeps its serialization and digest. The lane version is part of the policy: a lane whose rules
    change is registered under a new version, and a policy naming a version the code does not
    register is refused at selection."""

    lane_id: LaneId = "LEXICAL_SUBSTRING_V1"
    lane_version: Literal["1.0.0"] = "1.0.0"


class RetrievalLanePolicy(DomainModel):
    """Retrieval policy 3.0.0: the 2.0.0 budgets plus the lane that ranks candidates.

    RetrievalBehaviorPolicy 2.0.0 is not changed: adding a field to it would change the digest of
    every stored 2.0.0 policy. Version 3.0.0 reaches a run only through the existing shadow and
    canary exposure; the baseline stays 2.0.0.
    """

    kind: Literal["RETRIEVAL_POLICY"] = "RETRIEVAL_POLICY"
    version: Literal["3.0.0"] = "3.0.0"
    max_spans: int = Field(default=160, ge=1, le=160)
    character_budget: int = Field(default=50000, ge=1, le=50000)
    lanes: tuple[RetrievalLaneSpec, ...] = (RetrievalLaneSpec(),)

    @model_validator(mode="after")
    def _one_lane_for_now(self) -> "RetrievalLanePolicy":
        if len(self.lanes) != 1:
            raise ValueError("RETRIEVAL_POLICY_ONE_LANE_SUPPORTED")
        return self


class GlossaryPin(DomainModel):
    """The glossary a lane is pinned to, and how sure an entry must be to be used.

    The digest is over every entry of the list; a selection made with a list of another digest is
    refused. There is no default confidence: neither floor is blessed before it is measured.
    """

    digest: Sha256
    min_confidence: GlossaryConfidence


class FusedLaneSpec(DomainModel):
    """One lane of a fused retrieval policy and the fixed number of ranked chunks it contributes.

    A chunk is one ranked span for a span lane and one structure group for GROUPED_BM25; the depth
    cuts the lane's own ranking before the lanes are merged, so it does not depend on the others.
    """

    lane_id: FusedLaneId
    lane_version: Literal["1.0.0"] = "1.0.0"
    depth: int = Field(ge=1, le=160)
    # Only the glossary lane has it, and it must: the glossary the lane was made for.
    glossary: GlossaryPin | None = None

    @model_validator(mode="after")
    def _glossary_belongs_to_its_lane(self) -> "FusedLaneSpec":
        if (self.lane_id == "GLOSSARY_BM25") != (self.glossary is not None):
            raise ValueError("RETRIEVAL_POLICY_GLOSSARY_LANE_NEEDS_A_GLOSSARY")
        return self

    @model_serializer(mode="wrap")
    def _omit_no_glossary(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data = cast(dict[str, Any], handler(self))
        if self.glossary is None:
            data.pop("glossary", None)
        return data


class FusionMethodSpec(DomainModel):
    """How the lanes of a fused policy are merged; a new method is a new id or version."""

    method_id: Literal["ROUND_ROBIN_DEDUP"] = "ROUND_ROBIN_DEDUP"
    method_version: Literal["1.0.0"] = "1.0.0"


class RetrievalFusionPolicy(DomainModel):
    """Retrieval policy 4.0.0: two to four lanes, each with a fixed depth, merged by one method.

    The lanes are required and have no default, so no combination is blessed before it is measured.
    The order of the lanes is part of the policy. 2.0.0 and 3.0.0 are not changed. Like 3.0.0 it
    reaches a run only through shadow and canary exposure.
    """

    kind: Literal["RETRIEVAL_POLICY"] = "RETRIEVAL_POLICY"
    version: Literal["4.0.0"] = "4.0.0"
    max_spans: int = Field(default=160, ge=1, le=160)
    character_budget: int = Field(default=50000, ge=1, le=50000)
    lanes: tuple[FusedLaneSpec, ...] = Field(min_length=2, max_length=4)
    fusion: FusionMethodSpec = FusionMethodSpec()

    @model_validator(mode="after")
    def _distinct_lanes(self) -> "RetrievalFusionPolicy":
        if len({lane.lane_id for lane in self.lanes}) != len(self.lanes):
            raise ValueError("RETRIEVAL_POLICY_LANE_DUPLICATED")
        return self


class WorkflowBehaviorPolicy(DomainModel):
    kind: Literal["WORKFLOW_DEFINITION"] = "WORKFLOW_DEFINITION"
    version: Literal["2.0.0"] = "2.0.0"
    max_semantic_repairs: Literal[0, 1] = 1


RetrievalPolicy = RetrievalBehaviorPolicy | RetrievalLanePolicy | RetrievalFusionPolicy


def is_retrieval_policy(value: object) -> TypeGuard[RetrievalPolicy]:
    """The one place that says which policy classes are retrieval policies."""
    return isinstance(value, RetrievalBehaviorPolicy | RetrievalLanePolicy | RetrievalFusionPolicy)


def _policy_tag(value: object) -> str | None:
    """Pick the policy class by (kind, version). Stored 2.0.0 policies may omit the version."""
    if isinstance(value, dict):
        data = cast(dict[str, object], value)
        kind, version = data.get("kind"), data.get("version", "2.0.0")
    else:
        kind, version = getattr(value, "kind", None), getattr(value, "version", None)
    return f"{kind}:{version}" if isinstance(kind, str) and isinstance(version, str) else None


BehaviorPolicy = Annotated[
    Annotated[PromptBehaviorPolicy, Tag("PROMPT_BUNDLE:2.0.0")]
    | Annotated[RetrievalBehaviorPolicy, Tag("RETRIEVAL_POLICY:2.0.0")]
    | Annotated[RetrievalLanePolicy, Tag("RETRIEVAL_POLICY:3.0.0")]
    | Annotated[RetrievalFusionPolicy, Tag("RETRIEVAL_POLICY:4.0.0")]
    | Annotated[WorkflowBehaviorPolicy, Tag("WORKFLOW_DEFINITION:2.0.0")],
    Discriminator(_policy_tag),
]


class BehaviorPolicyError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


_TOKEN = re.compile(r"[^\W_]{3,}", re.UNICODE)
_STOP = {
    "and",
    "are",
    "for",
    "from",
    "how",
    "the",
    "this",
    "under",
    "what",
    "when",
    "where",
    "which",
    "with",
}


class EvidenceContextSelection(DomainModel):
    selected: tuple[EvidenceSpan, ...]
    forced_refs: tuple[str, ...]
    excluded_count: int = Field(ge=0)
    selected_characters: int = Field(ge=0)
    max_spans: int = Field(ge=1)
    character_budget: int = Field(ge=1)


def select_evidence_context(
    *,
    problem: str,
    evidence: tuple[EvidenceSpan, ...],
    forced_refs: tuple[str, ...] = (),
    max_spans: int = 160,
    character_budget: int = 50_000,
) -> EvidenceContextSelection:
    if max_spans < 1 or character_budget < 1:
        raise ValueError("evidence context budgets must be positive")
    admissible = connected_retrieval_spans(evidence)
    by_id = {span.span_id: span for span in admissible}
    missing_forced = tuple(reference for reference in forced_refs if reference not in by_id)
    if missing_forced:
        raise ValueError(f"forced evidence references are missing: {missing_forced}")
    query_tokens = _tokens(problem)
    scored = sorted(
        admissible,
        key=lambda span: (
            -_score(span, query_tokens),
            span.artifact_id,
            span.locator.page or 0,
            span.locator.line or 0,
            span.span_id,
        ),
    )
    selected: list[EvidenceSpan] = []
    selected_ids: set[str] = set()
    characters = 0
    for reference in dict.fromkeys(forced_refs):
        span = by_id[reference]
        selected.append(span)
        selected_ids.add(span.span_id)
        characters += len(span.exact_text)
    if len(selected) > max_spans or characters > character_budget:
        raise ValueError("forced evidence exceeds context budget")
    for span in scored:
        if span.span_id in selected_ids:
            continue
        size = len(span.exact_text)
        if len(selected) >= max_spans or characters + size > character_budget:
            continue
        selected.append(span)
        selected_ids.add(span.span_id)
        characters += size
    return EvidenceContextSelection(
        selected=tuple(selected),
        forced_refs=tuple(dict.fromkeys(forced_refs)),
        excluded_count=len(evidence) - len(selected),
        selected_characters=characters,
        max_spans=max_spans,
        character_budget=character_budget,
    )


def _tokens(value: str) -> frozenset[str]:
    return frozenset(
        token
        for token in (match.group(0).lower() for match in _TOKEN.finditer(value))
        if token not in _STOP
    )


def _score(span: EvidenceSpan, query_tokens: frozenset[str]) -> int:
    text_tokens = _tokens(span.exact_text)
    overlap = len(query_tokens & text_tokens)
    authority = (
        2 if span.authority_state in {AuthorityState.OFFICIAL, AuthorityState.APPROVED} else 0
    )
    cutoff = 1 if span.cutoff_state == CutoffState.ELIGIBLE else -10
    return overlap * 5 + authority + cutoff


def render_prompt_policy(policy: PromptBehaviorPolicy) -> dict[str, str]:
    return {} if not policy.task_guidance else {"behavior_guidance": policy.task_guidance}


def workflow_repair_decision(policy: WorkflowBehaviorPolicy, needs_repair: bool) -> str:
    if not needs_repair:
        return "NOT_NEEDED"
    return "REPAIR" if policy.max_semantic_repairs else "HELD"


class RetrievalGroupInput(DomainModel):
    """One structure group of an evaluation case: span ids that title it and span ids it holds."""

    kind: Literal["SECTION", "FENCE", "LIST", "BLOCK"]
    title_refs: tuple[str, ...] = Field(default=(), max_length=200)
    body_refs: tuple[str, ...] = Field(min_length=1, max_length=200)


class RetrievalCatalogInput(DomainModel):
    """What the structure observation says about the spans of an evaluation case.

    The field "groups" is written only when a comparison uses GROUPED_BM25. When it is empty it is
    not serialized, so a case that never named groups has the bytes, the digest and the comparison
    identity it always had.
    """

    ordered_refs: tuple[str, ...] = Field(default=(), max_length=20_000)
    protected_refs: tuple[str, ...] = Field(default=(), max_length=20_000)
    all_observed: bool = False
    structure_count: int = Field(default=0, ge=0)
    groups: tuple[RetrievalGroupInput, ...] = Field(default=(), max_length=20_000)

    @model_serializer(mode="wrap")
    def _omit_empty_groups(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data = cast(dict[str, Any], handler(self))
        if not self.groups:
            data.pop("groups", None)
        return data


class RetrievalGlossaryEntryInput(DomainModel):
    entry_id: str = Field(min_length=16, max_length=16)
    ko: str = Field(min_length=1, max_length=200)
    en: str = Field(min_length=1, max_length=200)
    domain: str = Field(min_length=1, max_length=200)
    confidence: GlossaryConfidence

    @model_validator(mode="after")
    def _id_is_the_content_hash(self) -> "RetrievalGlossaryEntryInput":
        if not entry_matches_its_id(self.entry()):
            raise ValueError("RETRIEVAL_CASE_GLOSSARY_ENTRY_INVALID")
        return self

    def entry(self) -> GlossaryEntry:
        return GlossaryEntry(self.entry_id, self.ko, self.en, self.domain, self.confidence)


class RetrievalGlossaryInput(DomainModel):
    """The glossary of an evaluation case: the digest of the whole list it was made from and only
    the entries that can matter for the case. Each entry is checked against its own id; the digest
    is claimed, not recomputed, and the evaluation output says so."""

    digest: Sha256
    entries: tuple[RetrievalGlossaryEntryInput, ...] = Field(max_length=500)

    @model_validator(mode="after")
    def _entries_are_distinct(self) -> "RetrievalGlossaryInput":
        if len({e.entry_id for e in self.entries}) != len(self.entries):
            raise ValueError("RETRIEVAL_CASE_GLOSSARY_ENTRY_DUPLICATED")
        return self


class RetrievalBehaviorInput(DomainModel):
    """An evaluation case. The optional fields carry what the research path also receives.

    forced_refs keeps its old name and means the same as pinned_refs: the research path's rule is
    that a pinned span is read first when it is eligible and is otherwise ignored.
    """

    problem: str
    evidence: tuple[EvidenceSpan, ...] = Field(max_length=20_000)
    forced_refs: tuple[str, ...] = Field(default=(), max_length=160)
    pinned_refs: tuple[str, ...] = Field(default=(), max_length=160)
    queries: tuple[str, ...] = Field(default=(), max_length=32)
    catalog: RetrievalCatalogInput | None = None
    glossary: RetrievalGlossaryInput | None = None

    @model_serializer(mode="wrap")
    def _omit_no_glossary(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        # Written only when a case uses the glossary lane, so every other case keeps its bytes.
        data = cast(dict[str, Any], handler(self))
        if self.glossary is None:
            data.pop("glossary", None)
        return data

    @model_validator(mode="after")
    def _groups_name_known_spans_once(self) -> "RetrievalBehaviorInput":
        """A case that names groups must name spans of its own evidence, each in one body only."""
        if self.catalog is None or not self.catalog.groups:
            return self
        known = {span.span_id for span in self.evidence}
        given: set[str] = set()
        for group in self.catalog.groups:
            if not {*group.title_refs, *group.body_refs} <= known:
                raise ValueError("RETRIEVAL_CASE_GROUP_SPAN_UNKNOWN")
            if given & set(group.body_refs) or len(set(group.body_refs)) != len(group.body_refs):
                raise ValueError("RETRIEVAL_CASE_GROUP_BODY_DUPLICATED")
            given.update(group.body_refs)
        return self


class WorkflowBehaviorInput(DomainModel):
    needs_repair: bool
