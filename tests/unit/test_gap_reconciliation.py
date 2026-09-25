import hashlib
from typing import Literal

import pytest

from thoth.application.services.research_coverage import assess_coverage
from thoth.application.services.research_gaps import gap_targets, validate_gaps
from thoth.domain.artifact import SourceLocator, StructuralRelation
from thoth.domain.enums import AuthorityState, CutoffState, SupportState, VerificationState
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.evidence_bundle import ContextStructureRef, EvidenceContextBundle
from thoth.domain.evidence_gap import GapProposal, GapReviewDecision, GapTarget
from thoth.domain.evidence_requirements import (
    EvidenceRanking,
    EvidenceRequirement,
    RequirementSetRevision,
    ReviewAdjudication,
    ReviewProposal,
    SemanticReviewCandidate,
    SemanticReviewDecision,
)
from thoth.domain.research_request import RevisionRef


def fixture(kind: Literal["BOUND_OBLIGATION", "RESEARCH_CHECK"] = "BOUND_OBLIGATION"):
    request = RevisionRef(
        project_id="p",
        entity_type="THREAD",
        entity_id="request:t",
        revision_id="r1",
        revision_digest="a" * 64,
    )
    ref = RevisionRef(
        project_id="p",
        entity_type="DECISION_OBJECT",
        entity_id="requirements:o",
        revision_id="r2",
        revision_digest="b" * 64,
    )
    req = EvidenceRequirement(
        requirement_id="r",
        kind=kind,
        target="answer",
        rule_refs=("rule",),
        question="Check context",
        rationale="required",
        needed_for="answer",
        blocker="answer:HOLD",
        followup="read caption",
    )
    requirements = RequirementSetRevision(
        request_ref=request,
        profile_ref="DOCUMENT_QUESTION:1",
        requirements=(req,),
        mandatory_rule_coverage={"rule": "r"},
        generation_run="planner",
    )
    text = "Table 9. Response timing"
    sha = hashlib.sha256(text.encode()).hexdigest()
    span = EvidenceSpan(
        span_id="s",
        project_id="p",
        artifact_id="a",
        source_version_id="v1",
        locator=SourceLocator(page=1),
        exact_text=text,
        text_sha256=sha,
        extraction_method="fixture",
        support_state=SupportState.EXTRACTED,
        authority_state=AuthorityState.INFORMAL,
        verification_state=VerificationState.SCHEMA_VALID,
        cutoff_state=CutoffState.ELIGIBLE,
    )
    node = ContextStructureRef(
        node_id="caption",
        kind="CAPTION",
        parent_id=None,
        locator=span.locator,
        text_digest=sha,
        relations=(StructuralRelation(kind="CAPTION_FOR", target_id="table"),),
    )
    bundle = EvidenceContextBundle(
        bundle_id="bundle",
        project_id="p",
        artifact_id="a",
        source_version_id="v1",
        container_id="table",
        anchor_span_refs=("s",),
        span_refs=("s",),
        span_digests={"s": sha},
        structures=(
            node,
            ContextStructureRef(
                node_id="table",
                kind="TABLE",
                parent_id=None,
                locator=SourceLocator(page=2),
                text_digest=None,
            ),
        ),
        basis_digest="c" * 64,
    )
    target = GapTarget(
        gap_id="g",
        omission_text="Caption missing",
        requirement_id="r",
        required_relation_kinds=("CAPTION_FOR",),
        origin="RERANKER",
    )
    proposal = GapProposal(
        gap_id="g",
        omission_text=target.omission_text,
        requirement_id="r",
        proposed_disposition="RESOLVED",
        proposed_span_refs=("s",),
        proposed_structure_refs=("caption",),
        source_version_ids=("v1",),
        explanation="caption found",
    )
    review = GapReviewDecision(
        gap_id="g",
        verdict="APPLIED",
        disposition="RESOLVED",
        requirement_set_digest=ref.revision_digest,
        basis_span_refs=("s",),
        explanation="checked",
    )
    candidate = ReviewProposal(
        candidates=(
            SemanticReviewCandidate(
                requirement_id="r",
                evidence_refs=("s",),
                relation="SUPPORTS",
                applicability="APPLICABLE",
                explanation="fixture",
                uncertainty="fixture",
                conditions_checked=True,
                time_checked=True,
            ),
        ),
        answer="fixture",
        gap_proposals=(proposal,),
    )
    adjudication = ReviewAdjudication(
        decisions=(
            SemanticReviewDecision(requirement_id="r", verdict="APPLIED", explanation="checked"),
        ),
        decomposition_complete=True,
        gap_decisions=(review,),
    )
    return requirements, ref, span, bundle, target, candidate, adjudication


@pytest.mark.parametrize(
    "variant",
    [
        "resolved",
        "missing_review",
        "wrong_version",
        "wrong_requirement_basis",
        "bound_waiver",
        "inferred_unconfirmed",
        "duplicate",
    ],
)
def test_gap_needs_current_source_and_explicit_review(variant: str) -> None:
    req, ref, span, bundle, target, candidate, adjudication = fixture()
    proposal = candidate.gap_proposals[0]
    decision = adjudication.gap_decisions[0]
    if variant == "missing_review":
        adjudication = adjudication.model_copy(update={"gap_decisions": ()})
    elif variant == "wrong_version":
        candidate = candidate.model_copy(
            update={"gap_proposals": (proposal.model_copy(update={"source_version_ids": ("v2",)}),)}
        )
    elif variant == "wrong_requirement_basis":
        adjudication = adjudication.model_copy(
            update={
                "gap_decisions": (decision.model_copy(update={"requirement_set_digest": "d" * 64}),)
            }
        )
    elif variant == "bound_waiver":
        candidate = candidate.model_copy(
            update={
                "gap_proposals": (
                    proposal.model_copy(update={"proposed_disposition": "NOT_REQUIRED"}),
                )
            }
        )
        adjudication = adjudication.model_copy(
            update={"gap_decisions": (decision.model_copy(update={"disposition": "NOT_REQUIRED"}),)}
        )
    elif variant == "inferred_unconfirmed":
        bundle = bundle.model_copy(
            update={
                "structures": (
                    bundle.structures[0].model_copy(
                        update={
                            "relations": (
                                StructuralRelation(
                                    kind="CAPTION_FOR", target_id="table", basis="INFERRED"
                                ),
                            )
                        }
                    ),
                    bundle.structures[1],
                )
            }
        )
    elif variant == "duplicate":
        candidate = candidate.model_copy(update={"gap_proposals": (proposal, proposal)})
    gaps = validate_gaps(
        (target,), req, ref, candidate, adjudication, (span,), (bundle,), "adjudicator"
    )
    assert gaps[0].effective_disposition == ("RESOLVED" if variant == "resolved" else "UNRESOLVED")
    coverage, _ = assess_coverage(
        req, ref, candidate, adjudication, (span,), "adjudicator", False, gap_validations=gaps
    )
    assert coverage.gates["answer"] == ("ASSESSED" if variant == "resolved" else "HOLD")


def test_optional_gap_can_be_not_required_but_omitting_legacy_warning_never_clears_it() -> None:
    req, ref, span, bundle, target, candidate, adjudication = fixture("RESEARCH_CHECK")
    candidate = candidate.model_copy(
        update={
            "gap_proposals": (
                candidate.gap_proposals[0].model_copy(
                    update={"proposed_disposition": "NOT_REQUIRED"}
                ),
            )
        }
    )
    adjudication = adjudication.model_copy(
        update={
            "gap_decisions": (
                adjudication.gap_decisions[0].model_copy(update={"disposition": "NOT_REQUIRED"}),
            )
        }
    )
    result = validate_gaps((target,), req, ref, candidate, adjudication, (span,), (bundle,), "run")
    assert result[0].effective_disposition == "NOT_REQUIRED"
    targets = gap_targets(
        EvidenceRanking(
            ordered_span_ids=("s",),
            rationale="fixture",
            omitted_required_information=("Unmapped context missing",),
        ),
        ref,
    )
    empty = candidate.model_copy(update={"gap_proposals": (), "missing_required_scope": ()})
    unresolved = validate_gaps(targets, req, ref, empty, adjudication, (span,), (bundle,), "run")
    assert unresolved[0].effective_disposition == "UNRESOLVED"
    coverage, _ = assess_coverage(
        req, ref, empty, adjudication, (span,), "run", False, gap_validations=unresolved
    )
    assert "GAP_MAPPING_INCOMPLETE" in coverage.reasons
