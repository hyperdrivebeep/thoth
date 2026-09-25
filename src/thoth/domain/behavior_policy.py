"""Bounded executable policies; fixed authority and scientific guards stay outside them."""

import re
from typing import Annotated, Literal

from pydantic import Field

from thoth.domain.base import DomainModel
from thoth.domain.enums import AuthorityState, CutoffState
from thoth.domain.evidence import EvidenceSpan, connected_retrieval_spans


class PromptBehaviorPolicy(DomainModel):
    kind: Literal["PROMPT_BUNDLE"] = "PROMPT_BUNDLE"
    version: Literal["2.0.0"] = "2.0.0"
    task_guidance: str = Field(default="", max_length=5000)


class RetrievalBehaviorPolicy(DomainModel):
    kind: Literal["RETRIEVAL_POLICY"] = "RETRIEVAL_POLICY"
    version: Literal["2.0.0"] = "2.0.0"
    max_spans: int = Field(default=160, ge=1, le=160)
    character_budget: int = Field(default=50000, ge=1, le=50000)


class WorkflowBehaviorPolicy(DomainModel):
    kind: Literal["WORKFLOW_DEFINITION"] = "WORKFLOW_DEFINITION"
    version: Literal["2.0.0"] = "2.0.0"
    max_semantic_repairs: Literal[0, 1] = 1


BehaviorPolicy = Annotated[
    PromptBehaviorPolicy | RetrievalBehaviorPolicy | WorkflowBehaviorPolicy,
    Field(discriminator="kind"),
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


class RetrievalBehaviorInput(DomainModel):
    problem: str
    evidence: tuple[EvidenceSpan, ...] = Field(max_length=20_000)
    forced_refs: tuple[str, ...] = Field(default=(), max_length=160)


class WorkflowBehaviorInput(DomainModel):
    needs_repair: bool
