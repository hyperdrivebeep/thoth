"""Lossless candidate details and explicit compatibility views of full Hypothesis records."""

from __future__ import annotations

from datetime import datetime

from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.hypothesis import Hypothesis, HypothesisPortfolio
from thoth.domain.hypothesis_full import HypothesisPortfolioRecord, HypothesisRecord
from thoth.domain.research_projection import HypothesisGenerationDetails, PortfolioGenerationDetails
from thoth.domain.test_validity import hypothesis_semantic_digest


def hypothesis_view(record: HypothesisRecord) -> Hypothesis | None:
    details = record.generation_details
    if details is None:
        return None
    return Hypothesis.model_validate(
        {
            "hypothesis_id": record.hypothesis_id,
            "object_id": record.object_id,
            "statement": record.statement,
            "observed_problem": record.observed_problem,
            "primary_locus": details.primary_locus,
            "contributing_loci": details.contributing_loci,
            "causal_depth": details.causal_depth,
            "scope_conditions": record.scope,
            "support_evidence_refs": record.evidence_refs,
            "counterevidence_refs": record.counterevidence_refs,
            "missing_evidence": details.missing_evidence,
            "counterevidence_queries": record.counterevidence_queries,
            "assumptions": details.assumptions,
            "uncertainty": details.uncertainty,
            "predicted_observations": details.predicted_observation_candidates,
            "discriminating_tests": details.discriminating_test_candidates,
            "status": details.candidate_status,
            "primary_intent": record.primary_intent,
            "critical_review": details.critical_review,
            "execution_appraisal": details.execution_appraisal,
            "prediction_proposal": details.prediction_proposal,
            "semantic_review_ref": details.semantic_review_ref,
        }
    )


def portfolio_view(
    record: HypothesisPortfolioRecord,
    hypotheses: tuple[HypothesisRecord, ...],
) -> tuple[HypothesisPortfolio | None, tuple[str, ...]]:
    by_id = {item.hypothesis_id: item for item in hypotheses}
    views: list[Hypothesis] = []
    unavailable: list[str] = []
    for reference in record.hypothesis_refs:
        hypothesis = by_id.get(reference)
        view = (
            None
            if hypothesis is None or hypothesis.object_id != record.object_id
            else hypothesis_view(hypothesis)
        )
        if view is None:
            unavailable.append(reference)
        else:
            views.append(view)
    details = record.generation_details
    if details is None:
        return None, tuple(dict.fromkeys((*unavailable, record.portfolio_id)))
    return HypothesisPortfolio(
        portfolio_id=record.portfolio_id,
        object_id=record.object_id,
        hypotheses=tuple(views),
        status=details.candidate_status,
        generated_from_head_set=details.generated_from_head_set,
        alternatives_considered=details.alternatives_considered,
        next_checks=details.next_checks,
        uncertainty_reserve=details.uncertainty_reserve,
    ), tuple(unavailable)


def full_hypothesis(
    candidate: Hypothesis,
    *,
    project_id: str,
    portfolio_id: str,
    revision_id: str,
    created_at: datetime,
    parent: str | None,
    current: HypothesisRecord | None = None,
) -> HypothesisRecord:
    intent = candidate.primary_intent
    if intent is None and current is not None:
        intent = current.primary_intent
    details = HypothesisGenerationDetails(
        primary_locus=candidate.primary_locus,
        contributing_loci=candidate.contributing_loci,
        causal_depth=candidate.causal_depth,
        missing_evidence=candidate.missing_evidence,
        assumptions=candidate.assumptions,
        uncertainty=candidate.uncertainty,
        predicted_observation_candidates=candidate.predicted_observations,
        discriminating_test_candidates=candidate.discriminating_tests,
        candidate_status=candidate.status,
        critical_review=candidate.critical_review,
        execution_appraisal=candidate.execution_appraisal,
        prediction_proposal=candidate.prediction_proposal,
        semantic_review_ref=candidate.semantic_review_ref,
    )
    draft: dict[str, object] = {} if current is None else current.model_dump(mode="python")
    invalidated = (
        ()
        if current is None
        else tuple(
            dict.fromkeys(
                (
                    *current.invalidated_refs,
                    *current.prediction_refs,
                    *current.test_refs,
                    *(("APPRAISALS",) if current.empirical_appraisal != "UNASSESSED" else ()),
                )
            )
        )
    )
    grounded = bool(
        intent is not None
        and candidate.support_evidence_refs
        and candidate.scope_conditions
        and (candidate.semantic_review_ref is None or candidate.status.value != "DRAFT")
    )
    profile: dict[str, object] = {"applicability": "UNASSESSED"}
    if intent is not None:
        profile = {"applicability": "NOT_APPLICABLE"}
        if intent in {"DIAGNOSTIC_CAUSAL", "EXPLANATORY_MECHANISTIC", "INTERVENTION_DESIGN"}:
            profile = {
                "applicability": "REQUIRED",
                "primary_locus": None
                if candidate.primary_locus is None
                else candidate.primary_locus.value,
                "causal_depth": candidate.causal_depth.value,
            }
    draft.update(
        {
            "hypothesis_revision_id": revision_id,
            "hypothesis_id": candidate.hypothesis_id,
            "project_id": project_id,
            "object_id": candidate.object_id,
            "portfolio_id": portfolio_id,
            "statement": candidate.statement,
            "observed_problem": candidate.observed_problem,
            "primary_intent": intent,
            "intent_profile_refs": ()
            if intent is None
            else (f"intent-profile:{intent.lower()}:1",),
            "evidence_basis": "MODEL_CANDIDATE",
            "scope": candidate.scope_conditions,
            "evidence_refs": candidate.support_evidence_refs,
            "counterevidence_refs": candidate.counterevidence_refs,
            "counterevidence_queries": candidate.counterevidence_queries,
            "causal_profile": profile,
            "development_stage": "GROUNDED_CANDIDATE" if grounded else "DRAFT",
            "empirical_appraisal": "UNASSESSED",
            "freshness": "CURRENT",
            "prediction_refs": (),
            "test_refs": (),
            "invalidated_refs": invalidated,
            "gateway_results": {
                "intent_profile": "INCOMPLETE" if intent is None else "PASS",
                "candidate_provenance": "MODEL_CANDIDATE",
                "sealed_prediction": "PENDING",
                "sealed_test": "PENDING",
            },
            "generation_details": details,
            "supersedes_revision_digest": parent,
            "created_at": created_at,
            "receipt_ref": None,
            "schema_version": "1.1.0",
            "revision_digest": "0" * 64,
        }
    )
    record = HypothesisRecord.model_validate(draft)
    if current is not None and hypothesis_semantic_digest(
        current.model_dump(mode="python")
    ) == hypothesis_semantic_digest(record.model_dump(mode="python")):
        record = HypothesisRecord.model_validate(
            {
                **record.model_dump(mode="python"),
                **{
                    key: getattr(current, key)
                    for key in (
                        "prediction_refs",
                        "test_refs",
                        "invalidated_refs",
                        "empirical_appraisal",
                        "development_stage",
                        "prespecification_state",
                        "gateway_results",
                    )
                },
            }
        )
    if current is not None and (
        hypothesis_semantic_digest(current.model_dump(mode="python"))
        != hypothesis_semantic_digest(record.model_dump(mode="python"))
        or current.evidence_refs != record.evidence_refs
        or current.counterevidence_refs != record.counterevidence_refs
    ):
        record = record.model_copy(
            update={
                "quality_profile": {key: "STALE" for key in current.quality_profile},
                "invalidated_refs": tuple(
                    dict.fromkeys((*record.invalidated_refs, "QUALITY_PROFILE"))
                ),
            }
        )
    digest = domain_digest(
        "HYPOTHESIS_RECORD",
        "2.0.0",
        canonical_payload(record.model_dump(mode="python", exclude={"revision_digest"})),
    )
    return record.model_copy(update={"revision_digest": digest})


def full_portfolio(
    candidate: HypothesisPortfolio,
    *,
    project_id: str,
    revision_id: str,
    created_at: datetime,
    parent: str | None,
    retained_refs: tuple[str, ...] = (),
    current: HypothesisPortfolioRecord | None = None,
) -> HypothesisPortfolioRecord:
    draft: dict[str, object] = {} if current is None else current.model_dump(mode="python")
    draft.update(
        {
            "portfolio_revision_id": revision_id,
            "portfolio_id": candidate.portfolio_id,
            "project_id": project_id,
            "object_id": candidate.object_id,
            "hypothesis_refs": tuple(
                dict.fromkeys(
                    (*retained_refs, *(item.hypothesis_id for item in candidate.hypotheses))
                )
            ),
            "unknown_reserve": {
                "state": "AVAILABLE",
                "reason": candidate.uncertainty_reserve,
                "alternatives_considered": candidate.alternatives_considered,
                "next_checks": candidate.next_checks,
            }
            if candidate.uncertainty_reserve != "UNASSESSED"
            else {"state": "NOT_ASSESSED"}
            if current is None
            else current.unknown_reserve,
            "generation_details": PortfolioGenerationDetails(
                candidate_status=candidate.status,
                generated_from_head_set=candidate.generated_from_head_set,
                alternatives_considered=candidate.alternatives_considered,
                next_checks=candidate.next_checks,
                uncertainty_reserve=candidate.uncertainty_reserve,
            ),
            "supersedes_revision_digest": parent,
            "created_at": created_at,
            "receipt_ref": None,
            "schema_version": "1.1.0",
            "revision_digest": "0" * 64,
        }
    )
    record = HypothesisPortfolioRecord.model_validate(draft)
    digest = domain_digest(
        "HYPOTHESIS_PORTFOLIO",
        "2.0.0",
        canonical_payload(record.model_dump(mode="python", exclude={"revision_digest"})),
    )
    return record.model_copy(update={"revision_digest": digest})
