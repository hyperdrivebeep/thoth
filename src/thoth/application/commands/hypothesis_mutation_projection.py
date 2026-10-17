"""Pure Hypothesis mutation payloads and responses; authority and writes stay in the root."""

from __future__ import annotations

from collections.abc import Container
from typing import cast

from pydantic import JsonValue

from thoth.application.commands.hypothesis_inputs import IntentUpdateInput
from thoth.application.services.revision_service import CommitResult
from thoth.domain.hypothesis_full import (
    AuxiliaryAssumptionRecord,
    HypothesisAppraisalRecord,
    HypothesisPortfolioRecord,
    HypothesisRecord,
    HypothesisRelationRecord,
    HypothesisTestBinding,
    PredictionRecord,
)


def created_hypothesis_result(
    item: HypothesisRecord, duplicates: tuple[str, ...], commit: CommitResult
) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        {
            "hypothesis": item.model_dump(mode="json"),
            "gateway_result": item.gateway_results,
            "duplicate_candidates": list(duplicates),
            "commit": commit.model_dump(mode="json"),
        },
    )


def added_relation_result(
    relation: HypothesisRelationRecord, revised: HypothesisPortfolioRecord, commit: CommitResult
) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        {
            "relation": relation.model_dump(mode="json"),
            "portfolio": revised.model_dump(mode="json"),
            "quality_impact": "REVALIDATION_REQUIRED",
            "discrimination_impact": "REVALIDATION_REQUIRED",
            "commit": commit.model_dump(mode="json"),
        },
    )


def removed_relation_result(
    ended: HypothesisRelationRecord, revised: HypothesisPortfolioRecord, commit: CommitResult
) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        {
            "relation": ended.model_dump(mode="json"),
            "portfolio": revised.model_dump(mode="json"),
            "commit": commit.model_dump(mode="json"),
        },
    )


def added_assumption_result(
    assumption: AuxiliaryAssumptionRecord, revised: HypothesisRecord, commit: CommitResult
) -> dict[str, JsonValue]:
    return {
        "assumption": assumption.model_dump(mode="json"),
        "hypothesis": revised.model_dump(mode="json"),
        "prediction_test_impact": list(revised.invalidated_refs),
        "commit": commit.model_dump(mode="json"),
    }


def bound_prediction_result(
    prediction: PredictionRecord, revised: HypothesisRecord, commit: CommitResult
) -> dict[str, JsonValue]:
    return {
        "prediction": prediction.model_dump(mode="json"),
        "hypothesis": revised.model_dump(mode="json"),
        "leakage_check": "PASS",
        "commit": commit.model_dump(mode="json"),
    }


def composed_portfolio_result(
    portfolio: HypothesisPortfolioRecord, commit: CommitResult
) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        {
            "portfolio": portfolio.model_dump(mode="json"),
            "duplicate_candidates": [],
            "quality_gaps": list(portfolio.quality_gaps),
            "discrimination_matrix_candidate": cast(
                JsonValue, list(portfolio.discrimination_matrix)
            ),
            "commit": commit.model_dump(mode="json"),
        },
    )


def revalidated_portfolio_result(
    revised: HypothesisPortfolioRecord, commit: CommitResult
) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        {
            "portfolio": revised.model_dump(mode="json"),
            "coverage": revised.coverage_state,
            "diversity": revised.diversity_state,
            "discrimination": revised.discrimination_state,
            "unknown_abstention": revised.abstention_state,
            "invalidations": list(revised.quality_gaps),
            "commit": commit.model_dump(mode="json"),
        },
    )


def bound_test_result(binding: HypothesisTestBinding) -> dict[str, JsonValue]:
    return {
        "test_binding": binding.model_dump(mode="json"),
        "appraisal_update_allowed": binding.appraisal_mutation_allowed,
        "affected_portfolio": None,
    }


def appraised_hypothesis_result(
    appraisal: HypothesisAppraisalRecord, revised: HypothesisRecord, commit: CommitResult
) -> dict[str, JsonValue]:
    return {
        "appraisal": appraisal.model_dump(mode="json"),
        "hypothesis": revised.model_dump(mode="json"),
        "commit": commit.model_dump(mode="json"),
    }


def appraised_quality_result(
    revised: HypothesisRecord, quality: dict[str, str], commit: CommitResult
) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        {
            "hypothesis": revised.model_dump(mode="json"),
            "epistemic_profile": quality,
            "commit": commit.model_dump(mode="json"),
        },
    )


def intent_update_payload(
    request: IntentUpdateInput, causal_intents: Container[str]
) -> dict[str, object]:
    return {
        "primary_intent": request.primary_intent,
        "secondary_intents": request.secondary_intents,
        "intent_profile_refs": request.intent_profile_refs,
        "causal_profile": {"applicability": "REQUIRED", "primary_locus": "UNCLASSIFIED"}
        if request.primary_intent in causal_intents
        else {"applicability": "NOT_APPLICABLE"},
    }


def quality_update_payload(current: HypothesisRecord) -> dict[str, str]:
    quality = dict(current.quality_profile)
    quality.update(
        {
            "counterevidence_readiness": "STRONG" if current.counterevidence_queries else "WEAK",
            "prediction_specificity": "STRONG" if current.prediction_refs else "WEAK",
            "testability": "STRONG" if current.test_refs else "MIXED",
            "discrimination": "MIXED" if current.prediction_refs else "NOT_ASSESSED",
        }
    )
    return quality
