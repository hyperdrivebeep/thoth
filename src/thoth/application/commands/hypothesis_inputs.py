"""Hypothesis RPC input models; original validation, inheritance and defaults."""

from __future__ import annotations

from pydantic import AwareDatetime, Field, JsonValue

from thoth.domain.base import DomainModel


class ProjectInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)


class HypothesisListInput(ProjectInput):
    object_id: str | None = Field(default=None, max_length=160)
    portfolio_id: str | None = Field(default=None, max_length=160)
    primary_intent: str | None = Field(default=None, max_length=80)
    stage: str | None = Field(default=None, max_length=80)
    appraisal: str | None = Field(default=None, max_length=80)
    freshness: str | None = Field(default=None, max_length=40)


class HypothesisReadInput(ProjectInput):
    hypothesis_id: str = Field(min_length=1, max_length=160)
    revision_digest: str | None = Field(default=None, min_length=64, max_length=64)


class PortfolioListInput(ProjectInput):
    object_id: str | None = Field(default=None, max_length=160)
    stage: str | None = Field(default=None, max_length=80)


class PortfolioReadInput(ProjectInput):
    portfolio_id: str = Field(min_length=1, max_length=160)
    revision_digest: str | None = Field(default=None, min_length=64, max_length=64)


class RelationListInput(ProjectInput):
    hypothesis_id: str | None = Field(default=None, max_length=160)
    portfolio_id: str | None = Field(default=None, max_length=160)
    relation_type: str | None = Field(default=None, max_length=80)


class PredictionListInput(ProjectInput):
    hypothesis_id: str | None = Field(default=None, max_length=160)
    prespecification_state: str | None = Field(default=None, max_length=80)
    observation_bound: bool | None = None


class PredictionReadInput(ProjectInput):
    prediction_id: str = Field(min_length=1, max_length=160)


class AssumptionListInput(ProjectInput):
    hypothesis_id: str | None = Field(default=None, max_length=160)
    prediction_id: str | None = Field(default=None, max_length=160)
    status: str | None = Field(default=None, max_length=80)


class GenerateInput(ProjectInput):
    object_id: str = Field(min_length=1, max_length=160)
    portfolio_id: str | None = Field(default=None, max_length=160)
    question: str = Field(min_length=1, max_length=10_000)
    evidence_scope: tuple[str, ...]
    intent_hints: tuple[str, ...] = ()
    generation_policy_ref: str = Field(min_length=1, max_length=160)
    budget_policy_ref: str | None = Field(default=None, max_length=160)


class CreateInput(ProjectInput):
    object_id: str = Field(min_length=1, max_length=160)
    portfolio_id: str = Field(min_length=1, max_length=160)
    statement: str = Field(min_length=1, max_length=10_000)
    primary_intent: str = Field(min_length=1, max_length=80)
    secondary_intents: tuple[str, ...] = ()
    evidence_basis: str = Field(min_length=1, max_length=2_000)
    scope: dict[str, str]
    evidence_refs: tuple[str, ...]
    expected_object_revision: str | None = Field(default=None, min_length=64, max_length=64)
    prespecification_state: str = Field(default="UNKNOWN", max_length=80)


class RevisionBoundInput(HypothesisReadInput):
    expected_revision_digest: str = Field(min_length=64, max_length=64)


class ReviseInput(RevisionBoundInput):
    patch: dict[str, JsonValue]
    evidence_refs: tuple[str, ...]
    reason: str = Field(min_length=1, max_length=5_000)


class IntentUpdateInput(RevisionBoundInput):
    primary_intent: str = Field(min_length=1, max_length=80)
    secondary_intents: tuple[str, ...]
    intent_profile_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    reason: str = Field(min_length=1, max_length=5_000)


class CausalUpdateInput(RevisionBoundInput):
    causal_patch: dict[str, JsonValue]
    evidence_refs: tuple[str, ...]
    reason: str = Field(min_length=1, max_length=5_000)


class RelationAddInput(ProjectInput):
    portfolio_id: str = Field(min_length=1, max_length=160)
    source_hypothesis_id: str = Field(min_length=1, max_length=160)
    relation_type: str = Field(min_length=1, max_length=80)
    target_hypothesis_id: str = Field(min_length=1, max_length=160)
    evidence_refs: tuple[str, ...]
    semantic_role: str | None = Field(default=None, max_length=500)
    expected_portfolio_revision: str = Field(min_length=64, max_length=64)


class RelationRemoveInput(ProjectInput):
    relation_id: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=5_000)
    evidence_refs: tuple[str, ...]
    expected_portfolio_revision: str = Field(min_length=64, max_length=64)


class AssumptionAddInput(RevisionBoundInput):
    statement: str = Field(min_length=1, max_length=5_000)
    role: str = Field(min_length=1, max_length=160)
    evidence_refs: tuple[str, ...]
    validation_route: str | None = Field(default=None, max_length=2_000)


class PredictionBindInput(ProjectInput):
    hypothesis_id: str = Field(min_length=1, max_length=160)
    hypothesis_revision_digest: str = Field(min_length=64, max_length=64)
    knowledge_cutoff: AwareDatetime
    prespecification_state: str = Field(min_length=1, max_length=80)
    conditions: dict[str, str]
    measurement_contract_ref: str = Field(min_length=1, max_length=260)
    assumption_refs: tuple[str, ...]
    expected_outcome: dict[str, JsonValue]
    discrimination_map: dict[str, JsonValue]


class CounterevidenceRequestInput(HypothesisReadInput):
    source_scope: tuple[str, ...]
    budget_policy_ref: str | None = Field(default=None, max_length=160)


class PortfolioComposeInput(ProjectInput):
    object_id: str = Field(min_length=1, max_length=160)
    hypothesis_ids: tuple[str, ...] = Field(min_length=2)
    relation_candidates: tuple[dict[str, JsonValue], ...] = ()
    unknown_reserve: dict[str, JsonValue]
    expected_object_revision: str | None = Field(default=None, min_length=64, max_length=64)
    portfolio_id: str | None = Field(default=None, max_length=160)


class PortfolioRevalidateInput(PortfolioReadInput):
    trigger_reason: str = Field(min_length=1, max_length=2_000)


class TestBindInput(ProjectInput):
    prediction_id: str = Field(min_length=1, max_length=160)
    execution_ref: str = Field(min_length=1, max_length=260)
    observation_refs: tuple[str, ...]
    test_validity_assessment_ref: str = Field(min_length=1, max_length=260)
    test_validity: str = Field(
        default="NOT_ASSESSABLE", pattern=r"^(VALID|LIMITED|INVALID|NOT_ASSESSABLE)$"
    )
    prediction_fit: str = Field(
        default="INCONCLUSIVE",
        pattern=r"^(MATCH|PARTIAL_MATCH|MISMATCH|NOT_OBSERVED|INCONCLUSIVE)$",
    )


class AppraiseInput(HypothesisReadInput):
    evidence_refs: tuple[str, ...]
    test_assessment_refs: tuple[str, ...]
    appraisal_scope: dict[str, str]


class SplitProposeInput(RevisionBoundInput):
    subhypotheses: tuple[dict[str, JsonValue], ...]
    evidence_refs: tuple[str, ...]
    rationale: str = Field(min_length=1, max_length=5_000)


class MergeProposeInput(ProjectInput):
    hypothesis_ids: tuple[str, ...] = Field(min_length=2)
    field_mapping: dict[str, JsonValue]
    evidence_refs: tuple[str, ...]
    rationale: str = Field(min_length=1, max_length=5_000)
    expected_revision_digests: tuple[str, ...] = Field(min_length=2)
