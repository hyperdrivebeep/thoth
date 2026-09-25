from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, Field, JsonValue

from thoth.domain.base import DomainModel
from thoth.domain.ids import ProjectId, Sha256, ThreadId


class CounterReviewTerminal(StrEnum):
    SUPPORTED = "SUPPORTED"
    ELIMINATED_WITHIN_SCOPE = "ELIMINATED_WITHIN_SCOPE"
    UNRESOLVED_NO_RESULTS = "UNRESOLVED_NO_RESULTS"
    UNRESOLVED_INDEPENDENCE = "UNRESOLVED_INDEPENDENCE"
    UNRESOLVED_AUTHORITY = "UNRESOLVED_AUTHORITY"
    UNRESOLVED_TEMPORAL = "UNRESOLVED_TEMPORAL"
    UNRESOLVED_PROHIBITED_CONTEXT = "UNRESOLVED_PROHIBITED_CONTEXT"
    UNRESOLVED_POLICY_BLOCKED = "UNRESOLVED_POLICY_BLOCKED"
    UNRESOLVED_FAILED = "UNRESOLVED_FAILED"
    UNRESOLVED_CONFLICT = "UNRESOLVED_CONFLICT"


class CounterLoopTerminal(StrEnum):
    SUPPORTED = "SUPPORTED"
    ELIMINATED_WITHIN_SCOPE = "ELIMINATED_WITHIN_SCOPE"
    SEARCH_SATURATED = "SEARCH_SATURATED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    AUTHORITY_REQUIRED = "AUTHORITY_REQUIRED"
    ABSTAINED = "ABSTAINED"


class CounterSearchTrack(DomainModel):
    track_id: str = Field(min_length=1, max_length=160)
    kind: str = Field(pattern=r"^(INDEPENDENT_SOURCE|ALTERNATIVE_EXPLANATION)$")
    query_family: str = Field(min_length=1, max_length=500)
    source_territory: str = Field(min_length=1, max_length=160)
    alternative_explanation: str = Field(min_length=1, max_length=2_000)


class CounterSearchPlan(DomainModel):
    plan_id: str = Field(min_length=1, max_length=160)
    project_id: ProjectId
    thread_id: ThreadId
    investigation_id: str = Field(min_length=1, max_length=160)
    hypothesis_id: str = Field(min_length=1, max_length=160)
    mode: Literal["CRITICAL"] = "CRITICAL"
    connector_id: str = Field(min_length=1, max_length=160)
    selector: dict[str, JsonValue]
    query_families: tuple[str, ...] = Field(min_length=1)
    tracks: tuple[CounterSearchTrack, ...] = Field(min_length=2, max_length=2)
    source_territory: str = Field(min_length=1, max_length=160)
    independence_group: str = Field(min_length=1, max_length=160)
    alternative_explanation_track: str = Field(min_length=1, max_length=2_000)
    source_authority: str = Field(min_length=1, max_length=80)
    temporal_state: str = Field(min_length=1, max_length=80)
    support_match_terms: tuple[str, ...] = ()
    counter_match_terms: tuple[str, ...] = ()
    context_tags: tuple[str, ...] = ()
    max_waves: int = Field(ge=1, le=3)
    max_results: int = Field(ge=1, le=20)
    policy_id: str = Field(min_length=1, max_length=160)
    policy_revision: int = Field(ge=1)
    policy_digest: Sha256
    challenger_version: str = "counterevidence-challenger:1.0.0"
    plan_digest: Sha256
    created_at: AwareDatetime
    route_id: str = Field(default="counter-route", min_length=1, max_length=160)
    priority: int = Field(default=50, ge=0, le=100)
    depth: int = Field(default=1, ge=0, le=8)
    excursion: bool = False
    checkpoint_after: bool = False
    estimated_cost_microunits: int = Field(default=1, ge=0, le=1_000_000)


class IndependentGateReview(DomainModel):
    review_id: str = Field(min_length=1, max_length=160)
    project_id: ProjectId
    hypothesis_id: str = Field(min_length=1, max_length=160)
    plan_id: str = Field(min_length=1, max_length=160)
    independence_state: str = Field(min_length=1, max_length=80)
    authority_state: str = Field(min_length=1, max_length=80)
    temporal_state: str = Field(min_length=1, max_length=80)
    context_state: str = Field(min_length=1, max_length=80)
    relevance_state: str = Field(min_length=1, max_length=80)
    terminal: CounterReviewTerminal
    reasons: tuple[str, ...] = Field(min_length=1)
    accepted_evidence_refs: tuple[str, ...] = ()
    executed_track_ids: tuple[str, ...] = ()
    reviewer_version: str = "independent-gate-reviewer:1.0.0"
    review_digest: Sha256
    reviewed_at: AwareDatetime


class HypothesisCriticalReview(DomainModel):
    investigation_id: str = Field(min_length=1, max_length=160)
    plan_id: str = Field(min_length=1, max_length=160)
    gate_review_id: str = Field(min_length=1, max_length=160)
    terminal: CounterReviewTerminal
    support_added_refs: tuple[str, ...] = ()
    counterevidence_added_refs: tuple[str, ...] = ()
    reasons: tuple[str, ...] = Field(min_length=1)
    review_digest: Sha256
    semantic_truth_certified: Literal[False] = False


class CounterWaveRecord(DomainModel):
    wave_index: int = Field(ge=1)
    route_id: str = Field(min_length=1, max_length=160)
    connector_id: str = Field(min_length=1, max_length=160)
    priority: int = Field(ge=0, le=100)
    depth: int = Field(ge=0, le=8)
    excursion: bool
    result_count: int = Field(ge=0)
    document_count: int = Field(ge=0)
    byte_count: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    cost_microunits: int = Field(ge=0)
    deduplicated: bool
    computed_voi: Decimal = Field(ge=0)
    sufficiency_before: str
    sufficiency_after: str
    decision_rank_before: int
    decision_rank_after: int
    gate_terminal: CounterReviewTerminal
    accepted_evidence_refs: tuple[str, ...] = ()
    executed_track_ids: tuple[str, ...] = ()
    checkpoint_digest: Sha256 | None = None


class CounterSearchBudgetState(DomainModel):
    max_waves: int
    max_results: int
    max_documents: int
    max_bytes: int
    max_model_calls: int
    max_tool_calls: int
    max_time_seconds: int
    max_cost_microunits: int
    max_depth: int
    used_waves: int = Field(ge=0)
    used_results: int = Field(ge=0)
    used_documents: int = Field(ge=0)
    used_bytes: int = Field(ge=0)
    used_model_calls: int = Field(ge=0)
    used_tool_calls: int = Field(ge=0)
    used_time_seconds: int = Field(ge=0)
    used_cost_microunits: int = Field(ge=0)
