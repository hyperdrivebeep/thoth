from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from thoth.application.services.action_projection import full_action
from thoth.application.services.hypothesis_projection import full_hypothesis, hypothesis_view
from thoth.domain.action import ActionCandidate
from thoth.domain.actor import ActorRef
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import (
    ActorKind,
    CausalDepth,
    CausalLocus,
    EntityType,
    ExecutionAuthority,
    HypothesisStatus,
    OutcomeStatus,
    Reversibility,
    RiskTier,
)
from thoth.domain.hypothesis import DiscriminatingTest, Hypothesis
from thoth.domain.hypothesis_full import HypothesisRecord
from thoth.domain.outcome import OutcomeRecord
from thoth.domain.research_identity import ResearchFamily, ResearchIdentityError, decode_research
from thoth.domain.revision import EntitySnapshot, SemanticRevision

NOW = datetime(2026, 9, 6, tzinfo=UTC)


def test_existing_action_authority_cannot_be_lowered_by_normal_candidate() -> None:
    protected = ActionCandidate(
        action_id="action:protected",
        object_id="object:identity",
        hypothesis_ids=("hypothesis:one",),
        action_family="CONTROLLED_WORK",
        specification="controlled work",
        expected_information_value="unknown",
        risk_tier=RiskTier.R3,
        execution_authority=ExecutionAuthority.HUMAN_REQUIRED_R3,
        reversibility=Reversibility.FULL,
        external_write=False,
        sandbox_required=False,
        required_approver_role="project-owner",
        source_refs=("span:known",),
    )
    current = full_action(
        protected,
        project_id="project:identity",
        portfolio_id="portfolio:action",
        revision_id="revision:protected",
        created_at=NOW,
        parent=None,
    )
    lowered = protected.model_copy(
        update={
            "risk_tier": RiskTier.R0,
            "execution_authority": ExecutionAuthority.AUTO_R0,
            "required_approver_role": None,
        }
    )
    with pytest.raises(ResearchIdentityError, match="RISK_DOWNGRADE"):
        full_action(
            lowered,
            project_id=current.project_id,
            portfolio_id=current.portfolio_id,
            revision_id="revision:lowered",
            created_at=NOW,
            parent=current.revision_digest,
            current=current,
        )
    assert current.authorization_state == "REQUIRED"


def test_process_outcome_is_not_reinterpreted_as_scientific_assessment() -> None:
    outcome = OutcomeRecord(
        outcome_id="outcome:process",
        project_id="project:identity",
        action_id="action:one",
        status=OutcomeStatus.OBSERVED,
        observed_evidence_refs=("span:known",),
        hypothesis_updates=(),
        interpretation="process observation only",
        limitations=("not scientific validity",),
        recorded_at=NOW,
        input_head_set_digest="0" * 64,
    )
    content = outcome.model_dump(mode="python")
    snapshot = EntitySnapshot(
        snapshot_id="snapshot:process",
        project_id=outcome.project_id,
        entity_type=EntityType.OUTCOME,
        entity_id=outcome.outcome_id,
        schema_version="1.0.0",
        content=content,
        content_digest=domain_digest("SNAPSHOT", "1.0.0", canonical_payload(content)),
    )
    revision = SemanticRevision(
        revision_id="revision:process",
        project_id=outcome.project_id,
        entity_type=EntityType.OUTCOME,
        entity_id=outcome.outcome_id,
        snapshot_id=snapshot.snapshot_id,
        parent_revision_digests=(),
        actor=ActorRef(actor_id="actor:process", kind=ActorKind.AGENT, role="fixture"),
        reason="process only",
        evidence_refs=(),
        affected_refs=(),
        revision_digest="d" * 64,
        created_at=NOW,
    )
    decoded = decode_research(revision, snapshot)
    assert decoded.identity.schema_family == ResearchFamily.OUTCOME_OBSERVATION
    assert isinstance(decoded.record, OutcomeRecord)
    assert "objective_attainment" not in decoded.record.model_dump()


def make_hypothesis(identifier: str, locus: CausalLocus) -> Hypothesis:
    return Hypothesis(
        hypothesis_id=identifier,
        object_id="object:identity",
        statement=f"Candidate {identifier}",
        observed_problem="Observed mismatch",
        primary_locus=locus,
        causal_depth=CausalDepth.INTERMEDIATE,
        scope_conditions={"condition": "recorded"},
        support_evidence_refs=("span:known",),
        counterevidence_refs=(),
        assumptions=("explicit assumption",),
        uncertainty="unresolved",
        predicted_observations=("candidate observation",),
        status=HypothesisStatus.TESTABLE,
        discriminating_tests=(
            DiscriminatingTest(
                test_id="test:candidate",
                procedure_candidate="compare",
                expected_if_true="different",
                expected_if_alternative="same",
                risk_tier=RiskTier.R1,
                reversibility=Reversibility.FULL,
            ),
        ),
    )


def test_full_projection_preserves_candidate_but_does_not_invent_intent_or_sealed_test() -> None:
    candidate = make_hypothesis("hypothesis:one", CausalLocus.INPUT_MATERIAL_DATA)
    record = full_hypothesis(
        candidate,
        project_id="project:identity",
        portfolio_id="portfolio:identity",
        revision_id="revision:one",
        created_at=NOW,
        parent=None,
    )
    assert record.primary_intent is None
    assert record.development_stage == "DRAFT"
    assert record.prediction_refs == record.test_refs == ()
    assert record.empirical_appraisal == "UNASSESSED"
    assert hypothesis_view(record) == candidate
    edited = record.model_copy(
        update={"statement": "Public edit", "scope": {"condition": "corrected"}}
    )
    view = hypothesis_view(edited)
    assert view is not None and view.statement == "Public edit"
    assert view.scope_conditions == {"condition": "corrected"}
    with pytest.raises(ValidationError):
        HypothesisRecord.model_validate(
            {**record.model_dump(), "development_stage": "GROUNDED_CANDIDATE"}
        )


def test_owner_metadata_rebinding_does_not_rewrite_historical_content() -> None:
    record = full_hypothesis(
        make_hypothesis("hypothesis:one", CausalLocus.INPUT_MATERIAL_DATA),
        project_id="project:identity",
        portfolio_id="portfolio:identity",
        revision_id="revision:old",
        created_at=NOW,
        parent=None,
    )
    content = record.model_dump(mode="python")
    snapshot = EntitySnapshot(
        snapshot_id="snapshot:restore",
        project_id=record.project_id,
        entity_type=EntityType.HYPOTHESIS,
        entity_id=record.hypothesis_id,
        schema_version="1.0.0",
        content=content,
        content_digest=domain_digest("SNAPSHOT", "1.0.0", canonical_payload(content)),
    )
    revision = SemanticRevision(
        revision_id="revision:restored",
        project_id=record.project_id,
        entity_type=EntityType.HYPOTHESIS,
        entity_id=record.hypothesis_id,
        snapshot_id=snapshot.snapshot_id,
        parent_revision_digests=("b" * 64,),
        actor=ActorRef(actor_id="actor:owner", kind=ActorKind.HUMAN, role="owner"),
        reason="restore",
        evidence_refs=(),
        affected_refs=(),
        revision_digest="a" * 64,
        created_at=NOW,
    )
    saved = canonical_payload(snapshot.content)
    decoded = decode_research(revision, snapshot)
    assert decoded.identity.schema_family == ResearchFamily.HYPOTHESIS
    assert isinstance(decoded.record, HypothesisRecord)
    assert decoded.record.revision_digest == revision.revision_digest
    assert decoded.record.hypothesis_revision_id == revision.revision_id
    assert decoded.record.supersedes_revision_digest == "b" * 64
    assert canonical_payload(snapshot.content) == saved
    assert snapshot.content["revision_digest"] == record.revision_digest
    with pytest.raises(ResearchIdentityError, match="DIGEST"):
        decode_research(revision, snapshot.model_copy(update={"content_digest": "f" * 64}))
