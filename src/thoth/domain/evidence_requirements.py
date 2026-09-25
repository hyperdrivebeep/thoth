"""Question-scoped requirements, relevance and reviewed support are separate axes."""

from typing import Literal, cast

from pydantic import Field, StrictBool, StrictInt, StrictStr, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.evidence_gap import GapProposal, GapReviewDecision, GapValidation
from thoth.domain.research_request import RevisionRef


class EvidenceRequirement(DomainModel):
    requirement_id: str
    kind: Literal["BOUND_OBLIGATION", "RESEARCH_CHECK"]
    target: str
    rule_refs: tuple[str, ...] = ()
    question: str
    rationale: str
    needed_for: str
    blocker: str
    followup: str
    timing: str = "BEFORE_TARGET_PROMOTION"
    applicability: str = "APPLICABLE"
    acceptable_evidence: tuple[str, ...] = ()
    time_requirement: str = "CURRENT_REQUEST_CUTOFF"
    counterevidence_required: bool = False
    governing_context: dict[str, object] = Field(default_factory=dict)


class RequirementProposal(DomainModel):
    profile_candidates: tuple[str, ...] = Field(
        description="One justified primary task profile from the supplied registry. "
        "Secondary aspects belong in checks. Multiple profiles mean an unresolved "
        "primary choice, not a combined workflow; explain that ambiguity."
    )
    checks: tuple[EvidenceRequirement, ...] = ()
    expanded_queries: tuple[str, ...] = ()
    unresolved_interpretations: tuple[str, ...] = ()
    explicit_public_search: bool = Field(
        default=False,
        description="True only when the user explicitly requests public lookup; "
        "respect negation and connected-only scope.",
    )
    connected_sources_only: bool = Field(
        default=False,
        description="True when the user restricts this request to connected material "
        "or forbids outside search.",
    )
    preferences: tuple[str, ...] = ()


class RequirementSetRevision(DomainModel):
    record_kind: Literal["RequirementSetRevision"] = "RequirementSetRevision"
    schema_version: Literal["2.0.0"] = "2.0.0"
    request_ref: RevisionRef
    profile_ref: str
    requirements: tuple[EvidenceRequirement, ...]
    mandatory_rule_coverage: dict[str, str]
    unresolved_profile_choices: tuple[str, ...] = ()
    generation_run: str


class EvidenceRanking(DomainModel):
    ordered_span_ids: tuple[str, ...]
    rationale: str
    omitted_required_information: tuple[str, ...] = ()
    gap_proposals: tuple[GapProposal, ...] = Field(
        default=(),
        description="Map each omitted item to a current requirement and known anchors; "
        "unresolved gaps are proposals, never automatic gates.",
    )


class SemanticReviewCandidate(DomainModel):
    requirement_id: str
    evidence_refs: tuple[str, ...] = ()
    relation: Literal["SUPPORTS", "REFUTES", "QUALIFIES", "IRRELEVANT", "INSUFFICIENT"]
    applicability: Literal["APPLICABLE", "UNKNOWN", "NOT_APPLICABLE_CANDIDATE"]
    explanation: str
    missing_context: tuple[str, ...] = ()
    counterevidence: tuple[str, ...] = ()
    uncertainty: str
    conditions_checked: bool = False
    time_checked: bool = False
    counterevidence_checked: bool = False
    applicability_basis: tuple[str, ...] = Field(
        default=(),
        description="Exact provided span IDs supporting applicability; never prose. "
        "Put explanation in explanation.",
    )


class ConflictCandidate(DomainModel):
    conflict_id: str
    requirement_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    description: str
    proposed_relevance: Literal["CURRENT_TARGET", "OPTIONAL_FOLLOWUP", "UNKNOWN"]


class ConflictReviewDecision(DomainModel):
    conflict_id: str
    verdict: Literal["APPLIED", "INCONCLUSIVE", "REJECTED"]
    resolution: Literal["UNRESOLVED", "RESOLVED", "NOT_RELEVANT"]
    basis_refs: tuple[str, ...]
    requirement_set_digest: str
    explanation: str


class ScopedConflictAssessment(DomainModel):
    request_ref: RevisionRef
    requirement_set_ref: RevisionRef
    conflict_id: str
    affected_targets: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    resolution: Literal["UNRESOLVED", "RESOLVED", "NOT_RELEVANT"]
    validation: str
    blocked_targets: tuple[str, ...]
    next_check: str
    candidate: ConflictCandidate | None = None
    decision: ConflictReviewDecision | None = None


class ReviewProposal(DomainModel):
    candidates: tuple[SemanticReviewCandidate, ...]
    answer: str
    unexamined_scope: tuple[str, ...] = Field(
        default=(),
        description="Informational scope limitations, including material outside "
        "the user's requested scope.",
    )
    missing_required_scope: tuple[str, ...] = Field(
        default=(),
        description="Only unexamined scope required to answer this request; "
        "do not list unrelated world knowledge.",
    )
    conflicts: tuple[str, ...] = ()
    scoped_conflicts: tuple[ConflictCandidate, ...] = ()
    gap_proposals: tuple[GapProposal, ...] = Field(
        default=(),
        description="Reconcile every supplied gap_id explicitly against the expanded source "
        "context. Cite actual spans/structure; omitting a gap does not resolve it.",
    )


class SemanticReviewDecision(DomainModel):
    requirement_id: str
    verdict: Literal["APPLIED", "INCONCLUSIVE", "REJECTED"]
    explanation: str
    applicability_confirmed: bool = False


class ReviewAdjudication(DomainModel):
    decisions: tuple[SemanticReviewDecision, ...]
    decomposition_complete: bool
    missing_requirements: tuple[str, ...] = ()
    gap_decisions: tuple[GapReviewDecision, ...] = Field(
        default=(),
        description="Separately confirm or reject each gap proposal on the exact "
        "requirement_set_ref digest. NOT_REQUIRED cannot waive a BOUND obligation; "
        "inferred structure relations require explicit confirmation.",
    )
    conflict_decisions: tuple[ConflictReviewDecision, ...] = ()


class SemanticReviewRecord(DomainModel):
    record_kind: Literal["SemanticReviewRecord"] = "SemanticReviewRecord"
    schema_version: Literal["2.0.0"] = "2.0.0"
    request_ref: RevisionRef
    target_digest: str
    candidate: SemanticReviewCandidate
    decision: SemanticReviewDecision
    structural_checks: tuple[str, ...]
    reviewer_run: str
    independence_group: str
    support_effect: str


class RequirementAssessment(DomainModel):
    requirement_id: str
    acquisition: str
    presence: str
    applicability: str
    relation: str
    validation: str
    resolution: Literal["SATISFIED", "UNRESOLVED", "NOT_APPLICABLE"]
    review_refs: tuple[str, ...] = ()
    blocker: str


class CoverageFields(DomainModel):
    record_kind: Literal["CoverageAssessment"] = "CoverageAssessment"
    request_ref: RevisionRef
    requirement_set_ref: RevisionRef
    assessments: tuple[RequirementAssessment, ...]
    web_decision: str
    reasons: tuple[str, ...]
    gates: dict[str, str]
    unexamined_scope: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    allowed_next_steps: tuple[str, ...] = ()


class LegacyCoverageAssessment(CoverageFields):
    schema_version: Literal["2.0.0"] = "2.0.0"


class CoverageAssessment(CoverageFields):
    schema_version: Literal["2.0.0", "2.1.0", "2.2.0"] = "2.2.0"
    scoped_conflicts: tuple[ScopedConflictAssessment, ...] = ()
    gap_validations: tuple[GapValidation, ...] = ()


class SelectorArgument(DomainModel):
    name: str = Field(min_length=1, max_length=160)
    value: StrictStr | StrictInt | StrictBool


class SourceSelector(DomainModel):
    connector_id: str = Field(min_length=1, max_length=160)
    selector: tuple[SelectorArgument, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="before")
    @classmethod
    def read_legacy_selector(cls, value: object) -> object:
        if isinstance(value, dict):
            mapping = cast(dict[str, object], value)
            selector = mapping.get("selector")
            if isinstance(selector, dict):
                entries = cast(dict[str, object], selector)
                return {
                    **mapping,
                    "selector": [{"name": k, "value": v} for k, v in entries.items()],
                }
            return mapping
        return value

    @model_validator(mode="after")
    def unique_fields(self) -> "SourceSelector":
        if len({arg.name for arg in self.selector}) != len(self.selector):
            raise ValueError("DUPLICATE_SELECTOR_FIELD")
        return self

    def values(self) -> dict[str, str | int | bool]:
        return {arg.name: arg.value for arg in self.selector}


class SourceCandidate(DomainModel):
    uri: str
    title: str
    snippet: str = ""


class ResearchSourcePlan(DomainModel):
    candidates: tuple[SourceCandidate, ...] = ()
    selectors: tuple[SourceSelector, ...] = ()
    reason: str


class HypothesisSemanticDecision(DomainModel):
    hypothesis_id: str
    relation: Literal["SUPPORTED", "QUALIFIED", "UNSUPPORTED", "INCONCLUSIVE"]
    evidence_refs: tuple[str, ...] = ()
    explanation: str
    gaps: tuple[str, ...] = ()


class HypothesisSemanticReview(DomainModel):
    decisions: tuple[HypothesisSemanticDecision, ...] = ()
    alternatives_considered: tuple[str, ...]
    next_checks: tuple[str, ...]
    uncertainty_reserve: str
    quality_axes: dict[str, str] = Field(default_factory=dict)


class HypothesisSemanticReviewRecord(DomainModel):
    record_kind: Literal["HypothesisSemanticReviewRecord"] = "HypothesisSemanticReviewRecord"
    schema_version: Literal["2.0.0"] = "2.0.0"
    request_ref: RevisionRef
    target_revision_digests: tuple[str, ...]
    portfolio_revision_digest: str
    review: HypothesisSemanticReview
    independence_group: str = "SAME_PRODUCT_MODEL_SEPARATE_ROLE"
    promotion: str = "EXISTING_GATEWAY_REQUIRED"
    quality_axes: str = "UNEVALUATED_UNLESS_RECORDED"
